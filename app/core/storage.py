import concurrent.futures
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import boto3
import firebase_admin
from botocore.config import Config
from firebase_admin import credentials, firestore

from app.config import settings
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.translation import TranslationJob
from app.services.book_bible_service import BookBibleService

logger = logging.getLogger("EpubBackend.StorageRepository")


class StorageRepository:
    """Firestore/memory persistence for jobs and append-only Bible timelines."""

    def __init__(self):
        self._jobs: Dict[str, TranslationJob] = {}
        self._bibles: Dict[str, BookBible] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.RLock()
        self.r2_enabled = False
        self.r2_client = None
        self.firebase_enabled = False
        self.firestore_db = None
        self._init_r2_storage()
        self._init_firebase()

    def _get_lock(self, doc_key: str) -> threading.Lock:
        if not hasattr(self, "_locks_guard"):
            self._locks_guard = threading.RLock()
        if not hasattr(self, "_locks"):
            self._locks = {}
        with self._locks_guard:
            if doc_key not in self._locks:
                self._locks[doc_key] = threading.RLock()
            return self._locks[doc_key]

    def _cache_bible(self, job_id: str, doc_key: str, bible: BookBible) -> None:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        self._bibles[job_id] = bible
        self._bibles[doc_key] = bible

    def _init_r2_storage(self):
        if (
            settings.cloudflare_account_id
            and settings.cloudflare_r2_access_key_id
            and settings.cloudflare_r2_secret_access_key
        ):
            try:
                endpoint_url = f"https://{settings.cloudflare_account_id}.r2.cloudflarestorage.com"
                self.r2_client = boto3.client(
                    "s3",
                    endpoint_url=endpoint_url,
                    aws_access_key_id=settings.cloudflare_r2_access_key_id,
                    aws_secret_access_key=settings.cloudflare_r2_secret_access_key,
                    config=Config(signature_version="s3v4"),
                    region_name="auto",
                )
                self.r2_enabled = True
            except Exception as exc:
                logger.warning("Cloudflare R2 init skipped: %s", exc)

    def _init_firebase(self):
        if not settings.firebase_enabled:
            logger.info("Firebase disabled by FIREBASE_ENABLED=false; using local memory storage.")
            return
        cred_path = settings.firebase_service_account_key
        cred_json_str = settings.firebase_credentials_json
        try:
            cred = None
            resolved_cred_path = cred_path
            if not os.path.isabs(resolved_cred_path):
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                resolved_cred_path = os.path.join(project_root, resolved_cred_path)
            if os.path.exists(resolved_cred_path):
                cred = credentials.Certificate(resolved_cred_path)
            elif cred_json_str:
                cred = credentials.Certificate(json.loads(cred_json_str))
            if cred:
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred)
                self.firestore_db = firestore.client()
                self.firebase_enabled = True
        except Exception as exc:
            logger.warning("Firebase init skipped: %s", exc)

    @property
    def is_r2_active(self) -> bool:
        return bool(getattr(self, "r2_enabled", False) and getattr(self, "r2_client", None))

    @property
    def is_firebase_active(self) -> bool:
        return bool(getattr(self, "firebase_enabled", False) and getattr(self, "firestore_db", None))

    def _r2_put_json(self, object_name: str, data: dict) -> bool:
        if not self.is_r2_active or not settings.cloudflare_r2_bucket_name:
            return False
        try:
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.r2_client.put_object(
                Bucket=settings.cloudflare_r2_bucket_name,
                Key=object_name,
                Body=body,
                ContentType="application/json; charset=utf-8",
            )
            return True
        except Exception as exc:
            logger.warning("Failed to put JSON to Cloudflare R2 (%s): %s", object_name, exc)
            return False

    def _r2_get_json(self, object_name: str) -> Optional[dict]:
        if not self.is_r2_active or not settings.cloudflare_r2_bucket_name:
            return None
        try:
            response = self.r2_client.get_object(
                Bucket=settings.cloudflare_r2_bucket_name,
                Key=object_name,
            )
            content = response["Body"].read().decode("utf-8")
            return json.loads(content)
        except Exception as exc:
            err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if err_code not in ("NoSuchKey", "404"):
                logger.warning("Failed to get JSON from Cloudflare R2 (%s): %s", object_name, exc)
            return None

    def _r2_list_json_objects(self, prefix: str) -> List[dict]:
        if not self.is_r2_active or not settings.cloudflare_r2_bucket_name:
            return []
        try:
            paginator = self.r2_client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=settings.cloudflare_r2_bucket_name, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if key.endswith(".json"):
                        keys.append(key)
            if not keys:
                return []
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(self._r2_get_json, k) for k in keys]
                for fut in concurrent.futures.as_completed(futures):
                    data = fut.result()
                    if data is not None:
                        results.append(data)
            return results
        except Exception as exc:
            logger.warning("Failed to list JSON objects from Cloudflare R2 (prefix=%s): %s", prefix, exc)
            return []

    def upload_file_to_r2(self, file_path: str, object_name: str) -> Optional[str]:
        if not self.is_r2_active or not settings.cloudflare_r2_bucket_name:
            return None
        try:
            self.r2_client.upload_file(
                file_path, settings.cloudflare_r2_bucket_name, object_name
            )
            if settings.cloudflare_r2_public_url:
                return f"{settings.cloudflare_r2_public_url.rstrip('/')}/{object_name}"
            return (
                f"https://{settings.cloudflare_account_id}.r2.cloudflarestorage.com/"
                f"{settings.cloudflare_r2_bucket_name}/{object_name}"
            )
        except Exception as exc:
            logger.error("Failed to upload file to Cloudflare R2: %s", exc)
            return None

    def save_job(self, job: TranslationJob) -> None:
        if not hasattr(self, "_jobs"):
            self._jobs = {}
        self._jobs[job.job_id] = job
        if self.is_r2_active:
            self._r2_put_json(f"data/jobs/{job.job_id}.json", job.model_dump(mode="json"))
        if self.is_firebase_active:
            try:
                ref = self.firestore_db.collection("translation_jobs").document(job.job_id)
                ref.set(job.model_dump(mode="json"), merge=True)
            except Exception as exc:
                logger.warning("Failed to sync job to Firestore, saved locally: %s", exc)

    def get_job(self, job_id: str) -> Optional[TranslationJob]:
        if not hasattr(self, "_jobs"):
            self._jobs = {}
        if self.is_firebase_active:
            try:
                ref = self.firestore_db.collection("translation_jobs").document(job_id)
                snapshot = ref.get()
                if snapshot.exists:
                    job = TranslationJob.model_validate(snapshot.to_dict())
                    self._jobs[job_id] = job
                    return job
            except Exception as exc:
                logger.warning("Failed to fetch job from Firestore, using local memory: %s", exc)
        if self.is_r2_active and job_id not in self._jobs:
            data = self._r2_get_json(f"data/jobs/{job_id}.json")
            if data:
                job = TranslationJob.model_validate(data)
                self._jobs[job_id] = job
                return job
        return self._jobs.get(job_id)

    @staticmethod
    def _as_delta(bible: BookBible) -> BookBibleDelta:
        return BookBibleDelta(
            new_characters=bible.characters,
            new_places=bible.places,
            new_terms=bible.terms,
            style_guide=bible.style_guide,
        )

    @staticmethod
    def _merge_full_bible(existing: Optional[BookBible], incoming: BookBible) -> BookBible:
        if existing is None:
            result = incoming.model_copy(deep=True)
            return BookBibleService.ensure_timeline(result)
        target = existing.model_copy(deep=True)
        if target.novel_id == "default" and incoming.novel_id != "default":
            target.novel_id = incoming.novel_id
        target = BookBibleService.merge_delta(target, StorageRepository._as_delta(incoming))
        observation_index = {
            item.observation_id: index
            for index, item in enumerate(target.address_observations)
        }
        for item in incoming.address_observations:
            if item.observation_id in observation_index:
                target.address_observations[observation_index[item.observation_id]] = item.model_copy(deep=True)
            else:
                target.address_observations.append(item.model_copy(deep=True))
                observation_index[item.observation_id] = len(target.address_observations) - 1
        change_index = {item.change_id: index for index, item in enumerate(target.pending_changes)}
        for item in incoming.pending_changes:
            if item.change_id in change_index:
                target.pending_changes[change_index[item.change_id]] = item.model_copy(deep=True)
            else:
                target.pending_changes.append(item.model_copy(deep=True))
                change_index[item.change_id] = len(target.pending_changes) - 1
        target.schema_version = max(target.schema_version, incoming.schema_version)
        target.bible_revision = max(target.bible_revision, incoming.bible_revision)
        return BookBibleService.ensure_timeline(target)

    def save_bible(self, job_id: str, bible: BookBible) -> None:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        started = time.perf_counter()
        doc_key = bible.novel_id if bible.novel_id and bible.novel_id != "default" else job_id
        lock = self._get_lock(doc_key)
        with lock:
            merge_started = time.perf_counter()
            existing = self._bibles.get(doc_key) or self._bibles.get(job_id)
            if existing is None and self.is_r2_active:
                r2_data = self._r2_get_json(f"data/bibles/{doc_key}.json")
                if r2_data:
                    existing = BookBibleService.ensure_timeline(BookBible.model_validate(r2_data))
            merged = self._merge_full_bible(existing, bible)
            self._cache_bible(job_id, doc_key, merged)
            logger.info(
                "[TIMING] stage=book_bible_merge_storage.end novel=%s elapsed_ms=%.1f "
                "characters=%d observations=%d",
                doc_key,
                (time.perf_counter() - merge_started) * 1000,
                len(merged.characters),
                len(merged.address_observations),
            )

            if self.is_r2_active:
                self._r2_put_json(f"data/bibles/{doc_key}.json", merged.model_dump(mode="json"))

            if self.is_firebase_active:
                try:
                    firestore_started = time.perf_counter()
                    ref = self.firestore_db.collection("book_bibles").document(doc_key)
                    ref.set(merged.model_dump(mode="json"), merge=True)
                    logger.info(
                        "[TIMING] stage=firestore_bible_write.end novel=%s elapsed_ms=%.1f",
                        doc_key,
                        (time.perf_counter() - firestore_started) * 1000,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist Book Bible to Firestore, saved locally: %s", exc)
            logger.info(
                "[TIMING] stage=book_bible_persist.total novel=%s elapsed_ms=%.1f",
                doc_key,
                (time.perf_counter() - started) * 1000,
            )

    def merge_bible_delta(
        self,
        job_or_novel_id: str,
        delta: BookBibleDelta,
        default_novel_id: Optional[str] = None,
    ) -> BookBible:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        lock = self._get_lock(job_or_novel_id)
        with lock:
            existing = self._bibles.get(job_or_novel_id)
            if existing is None and self.is_r2_active:
                r2_data = self._r2_get_json(f"data/bibles/{job_or_novel_id}.json")
                if r2_data:
                    existing = BookBibleService.ensure_timeline(BookBible.model_validate(r2_data))
            target = existing.model_copy(deep=True) if existing else BookBible(
                novel_id=default_novel_id or job_or_novel_id
            )
            merged = BookBibleService.merge_delta(target, delta)
            self._cache_bible(job_or_novel_id, job_or_novel_id, merged)

            if self.is_r2_active:
                self._r2_put_json(f"data/bibles/{job_or_novel_id}.json", merged.model_dump(mode="json"))

            if self.is_firebase_active:
                try:
                    ref = self.firestore_db.collection("book_bibles").document(job_or_novel_id)
                    ref.set(merged.model_dump(mode="json"), merge=True)
                except Exception as exc:
                    logger.warning("Failed to merge Bible delta to Firestore, saved locally: %s", exc)
            return merged

    def get_bible(self, job_id: str) -> Optional[BookBible]:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        if self.is_firebase_active:
            try:
                ref = self.firestore_db.collection("book_bibles").document(job_id)
                snapshot = ref.get()
                if snapshot.exists:
                    bible = BookBibleService.ensure_timeline(
                        BookBible.model_validate(snapshot.to_dict())
                    )
                    self._bibles[job_id] = bible
                    return bible
            except Exception as exc:
                logger.warning("Failed to fetch Book Bible from Firestore, using local memory: %s", exc)
        if self.is_r2_active and job_id not in self._bibles:
            data = self._r2_get_json(f"data/bibles/{job_id}.json")
            if data:
                bible = BookBibleService.ensure_timeline(BookBible.model_validate(data))
                self._bibles[job_id] = bible
                return bible
        bible = self._bibles.get(job_id)
        return BookBibleService.ensure_timeline(bible) if bible else None

    def review_pending_change(
        self,
        novel_id: str,
        change_id: str,
        status: str,
        reviewed_by: Optional[str] = None,
    ) -> Optional[BookBible]:
        lock = self._get_lock(novel_id)
        with lock:
            bible = self.get_bible(novel_id)
            if bible is None:
                return None
            change = next(
                (item for item in bible.pending_changes if item.change_id == change_id),
                None,
            )
            if change is None:
                return None
            if status not in {"approved", "rejected"}:
                raise ValueError("Invalid review status.")
            change.status = status
            change.reviewed_at = datetime.utcnow()
            change.reviewed_by = reviewed_by
            if status == "approved":
                for observation in bible.address_observations:
                    if observation.observation_id == (change.observation_id or change_id):
                        observation.resolution = "confirmed"
                if change.change_type == "canonical_correction":
                    for character in bible.characters:
                        if character.character_id == change.target_id:
                            character.vi_name = change.proposed_value
            bible.bible_revision += 1
            self.save_bible(novel_id, bible)
            return bible

    def list_jobs(self) -> List[TranslationJob]:
        if not hasattr(self, "_jobs"):
            self._jobs = {}
        if self.is_firebase_active:
            try:
                docs = self.firestore_db.collection("translation_jobs").stream()
                jobs = []
                for doc in docs:
                    job = TranslationJob.model_validate(doc.to_dict())
                    jobs.append(job)
                    self._jobs[job.job_id] = job
                return jobs
            except Exception as exc:
                logger.warning("Failed to list jobs from Firestore, falling back: %s", exc)
        if self.is_r2_active:
            items = self._r2_list_json_objects("data/jobs/")
            if items:
                jobs = []
                for data in items:
                    job = TranslationJob.model_validate(data)
                    jobs.append(job)
                    self._jobs[job.job_id] = job
                return jobs
        return list(self._jobs.values())

    def list_bibles(self) -> Dict[str, BookBible]:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        if self.is_firebase_active:
            try:
                docs = self.firestore_db.collection("book_bibles").stream()
                for doc in docs:
                    bible = BookBibleService.ensure_timeline(
                        BookBible.model_validate(doc.to_dict())
                    )
                    self._bibles[doc.id] = bible
                return self._bibles
            except Exception as exc:
                logger.warning("Failed to list Book Bibles from Firestore, falling back: %s", exc)
        if self.is_r2_active:
            items = self._r2_list_json_objects("data/bibles/")
            if items:
                for data in items:
                    bible = BookBibleService.ensure_timeline(BookBible.model_validate(data))
                    doc_id = bible.novel_id or "default"
                    self._bibles[doc_id] = bible
                return self._bibles
    def delete_bible(self, job_or_novel_id: str) -> bool:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        lock = self._get_lock(job_or_novel_id)
        with lock:
            self._bibles.pop(job_or_novel_id, None)
            if self.is_r2_active and settings.cloudflare_r2_bucket_name:
                try:
                    self.r2_client.delete_object(
                        Bucket=settings.cloudflare_r2_bucket_name,
                        Key=f"data/bibles/{job_or_novel_id}.json",
                    )
                except Exception as exc:
                    logger.warning("Failed to delete Book Bible from Cloudflare R2: %s", exc)
            if self.is_firebase_active:
                try:
                    self.firestore_db.collection("book_bibles").document(job_or_novel_id).delete()
                except Exception as exc:
                    logger.warning("Failed to delete Book Bible from Firestore: %s", exc)
            return True


storage_repo = StorageRepository()


