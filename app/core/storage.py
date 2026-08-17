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

    def upload_file_to_r2(self, file_path: str, object_name: str) -> Optional[str]:
        if not self.r2_enabled or not settings.cloudflare_r2_bucket_name:
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
        self._jobs[job.job_id] = job
        if self.firebase_enabled and self.firestore_db:
            try:
                ref = self.firestore_db.collection("translation_jobs").document(job.job_id)
                ref.set(job.model_dump(mode="json"), merge=True)
            except Exception as exc:
                logger.warning("Failed to sync job to Firestore, saved locally: %s", exc)

    def get_job(self, job_id: str) -> Optional[TranslationJob]:
        if self.firebase_enabled and self.firestore_db:
            try:
                ref = self.firestore_db.collection("translation_jobs").document(job_id)
                snapshot = ref.get()
                if snapshot.exists:
                    job = TranslationJob.model_validate(snapshot.to_dict())
                    self._jobs[job_id] = job
                    return job
            except Exception as exc:
                logger.warning("Failed to fetch job from Firestore, using local memory: %s", exc)
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
        started = time.perf_counter()
        doc_key = bible.novel_id if bible.novel_id and bible.novel_id != "default" else job_id
        lock = self._get_lock(doc_key)
        with lock:
            merge_started = time.perf_counter()
            existing = self._bibles.get(doc_key) or self._bibles.get(job_id)
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

            if self.firebase_enabled and self.firestore_db:
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
        lock = self._get_lock(job_or_novel_id)
        with lock:
            existing = self._bibles.get(job_or_novel_id)
            target = existing.model_copy(deep=True) if existing else BookBible(
                novel_id=default_novel_id or job_or_novel_id
            )
            merged = BookBibleService.merge_delta(target, delta)
            self._cache_bible(job_or_novel_id, job_or_novel_id, merged)

            if self.firebase_enabled and self.firestore_db:
                try:
                    ref = self.firestore_db.collection("book_bibles").document(job_or_novel_id)
                    ref.set(merged.model_dump(mode="json"), merge=True)
                except Exception as exc:
                    logger.warning("Failed to merge Bible delta to Firestore, saved locally: %s", exc)
            return merged

    def get_bible(self, job_id: str) -> Optional[BookBible]:
        if self.firebase_enabled and self.firestore_db:
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
        if self.firebase_enabled and self.firestore_db:
            try:
                docs = self.firestore_db.collection("translation_jobs").stream()
                jobs = []
                for doc in docs:
                    job = TranslationJob.model_validate(doc.to_dict())
                    jobs.append(job)
                    self._jobs[job.job_id] = job
                return jobs
            except Exception as exc:
                logger.warning("Failed to list jobs from Firestore, falling back to local memory: %s", exc)
        return list(self._jobs.values())

    def list_bibles(self) -> Dict[str, BookBible]:
        if self.firebase_enabled and self.firestore_db:
            try:
                docs = self.firestore_db.collection("book_bibles").stream()
                for doc in docs:
                    bible = BookBibleService.ensure_timeline(
                        BookBible.model_validate(doc.to_dict())
                    )
                    self._bibles[doc.id] = bible
                return self._bibles
            except Exception as exc:
                logger.warning("Failed to list Book Bibles from Firestore, falling back to local memory: %s", exc)
        return self._bibles


storage_repo = StorageRepository()


