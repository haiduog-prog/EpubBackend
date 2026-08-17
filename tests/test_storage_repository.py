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
    repository = StorageRepository.__new__(StorageRepository)
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
    repository = StorageRepository.__new__(StorageRepository)
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
