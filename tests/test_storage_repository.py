from app.core.storage import StorageRepository
from app.schemas.book_bible import BookBible, CharacterEntry
from app.schemas.translation import InputType, JobStatusEnum, TranslationJob


class FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeDocument:
    def __init__(self):
        self.data = None

    def set(self, data, merge=True):
        if merge and self.data:
            self.data.update(data)
        else:
            self.data = dict(data)

    def get(self):
        return FakeSnapshot(self.data)


class FakeCollection:
    def __init__(self):
        self.documents = {}

    def document(self, key):
        return self.documents.setdefault(key, FakeDocument())


class FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


def make_repository():
    repository = StorageRepository()
    repository._jobs = {}
    repository._bibles = {}
    repository.firebase_enabled = True
    repository.firestore_db = FakeFirestore()
    return repository


def test_get_job_prefers_firestore_over_stale_memory_cache():
    repository = make_repository()
    job = TranslationJob(
        job_id="job-1",
        filename="book.txt",
        input_type=InputType.TXT,
        status=JobStatusEnum.COMPLETED,
        progress_percentage=100,
    )
    repository._jobs[job.job_id] = job.model_copy(
        update={"status": JobStatusEnum.PROCESSING, "progress_percentage": 10}
    )
    repository.save_job(job)

    repository._jobs[job.job_id] = job.model_copy(
        update={"status": JobStatusEnum.PROCESSING, "progress_percentage": 1}
    )
    loaded = repository.get_job(job.job_id)

    assert loaded is not None
    assert loaded.status == JobStatusEnum.COMPLETED
    assert loaded.progress_percentage == 100


def test_save_and_get_bible_use_novel_document_key():
    repository = make_repository()
    bible = BookBible(
        novel_id="novel-1",
        characters=[
            CharacterEntry(original_name="Alice", vi_name="A-l?-s?")
        ],
    )

    repository.save_bible("job-1", bible)

    assert "novel-1" in repository.firestore_db.collection("book_bibles").documents
    loaded = repository.get_bible("novel-1")
    assert loaded is not None
    assert loaded.novel_id == "novel-1"
    assert loaded.characters[0].vi_name == "A-l?-s?"


def test_local_fallback_still_works_without_firestore():
    repository = StorageRepository()
    repository._jobs = {}
    repository._bibles = {}
    repository.firebase_enabled = False
    repository.firestore_db = None

    job = TranslationJob(
        job_id="job-local",
        filename="book.txt",
        input_type=InputType.TXT,
    )
    repository.save_job(job)

    assert repository.get_job("job-local") == job


def test_blob_reads_fallback_to_legacy_r2_when_supabase_misses(monkeypatch):
    from app.config import settings

    class StubProvider:
        def __init__(self, name, objects):
            self.provider_name = name
            self.objects = objects

        @property
        def is_active(self):
            return True

        def get_bytes(self, object_name, raise_on_error=False):
            return self.objects.get(object_name)

    monkeypatch.setattr(settings, "storage_provider", "supabase")
    repository = StorageRepository()
    repository.supabase_provider = StubProvider("supabase", {})
    repository.r2_provider = StubProvider("r2", {"novels/legacy/ch_0001.txt": b"legacy chapter"})
    repository.local_provider = StubProvider("local", {})

    assert repository.get_bytes("novels/legacy/ch_0001.txt") == b"legacy chapter"


class FakeR2Client:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        if isinstance(Body, bytes):
            self.objects[Key] = Body
        else:
            self.objects[Key] = Body.encode("utf-8")

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            err = Exception("NoSuchKey")
            err.response = {"Error": {"Code": "NoSuchKey"}}
            raise err
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, operation_name):
        class FakePaginator:
            def __init__(self, objects):
                self._objects = objects
            def paginate(self, Bucket, Prefix):
                contents = [{"Key": k} for k in self._objects.keys() if k.startswith(Prefix)]
                return [{"Contents": contents}]
        return FakePaginator(self.objects)


def make_r2_repository():
    from app.config import settings
    settings.storage_provider = "r2"
    settings.cloudflare_r2_bucket_name = "test-bucket"
    repository = StorageRepository()
    repository._jobs = {}
    repository._bibles = {}
    repository.firebase_enabled = False
    repository.firestore_db = None
    repository.r2_provider.r2_client = FakeR2Client()
    repository.r2_provider.r2_enabled = True
    repository.r2_provider.bucket_name = "test-bucket"
    return repository


def test_r2_save_and_get_job_persists_across_cache_clear():
    repository = make_r2_repository()
    job = TranslationJob(
        job_id="job-r2-1",
        filename="novel.epub",
        input_type=InputType.EPUB,
        status=JobStatusEnum.COMPLETED,
        progress_percentage=100.0,
    )
    repository.save_job(job)

    assert "data/jobs/job-r2-1.json" in repository.r2_client.objects

    # Simulate clearing in-memory cache (e.g. server restart)
    repository._jobs.clear()

    loaded = repository.get_job("job-r2-1")
    assert loaded is not None
    assert loaded.job_id == "job-r2-1"
    assert loaded.filename == "novel.epub"
    assert loaded.status == JobStatusEnum.COMPLETED


def test_r2_save_and_get_bible_persists_across_cache_clear():
    repository = make_r2_repository()
    bible = BookBible(
        novel_id="novel-r2-1",
        characters=[
            CharacterEntry(original_name="Alice", vi_name="A-lệ-ti")
        ],
    )
    repository.save_bible("job-r2-1", bible)

    assert ("novels/novel-r2-1/bible.json" in repository.r2_client.objects or "data/bibles/novel-r2-1.json" in repository.r2_client.objects)

    # Simulate clearing in-memory cache
    repository._bibles.clear()

    loaded = repository.get_bible("novel-r2-1")
    assert loaded is not None
    assert loaded.novel_id == "novel-r2-1"
    assert len(loaded.characters) == 1
    assert loaded.characters[0].vi_name == "A-lệ-ti"


def test_r2_list_jobs_and_bibles():
    repository = make_r2_repository()
    job1 = TranslationJob(job_id="job-list-1", filename="1.txt", input_type=InputType.TXT)
    job2 = TranslationJob(job_id="job-list-2", filename="2.txt", input_type=InputType.TXT)
    repository.save_job(job1)
    repository.save_job(job2)

    bible1 = BookBible(novel_id="bible-1")
    repository.save_bible("job-list-1", bible1)

    repository._jobs.clear()
    repository._bibles.clear()

    jobs = repository.list_jobs()
    assert len(jobs) == 2
    job_ids = {j.job_id for j in jobs}
    assert "job-list-1" in job_ids and "job-list-2" in job_ids

    bibles = repository.list_bibles()
    assert "bible-1" in bibles
