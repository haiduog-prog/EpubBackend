# Firebase Firestore setup

This project uses Firebase Firestore only. Firebase Storage is not initialized and is not required.

## 1. Create the Firebase project

1. Create a project in Firebase Console.
2. Create a Cloud Firestore database.
3. Keep the project on Spark if you only use Firestore within the free quota.
4. Do not enable Firebase Storage.

Firestore stores Book Bible, translation jobs, and progress. EPUB/TXT files stay in the local storage/ directory.

## 2. Create a backend service account

In Firebase Console:

1. Open Project settings.
2. Open Service accounts.
3. Generate a new private key.
4. Save it as serviceAccountKey.json in the project root.

The file is already covered by .gitignore. Never put it in the Android APK or commit it to Git.

You can configure the backend with:

~~~env
FIREBASE_SERVICE_ACCOUNT_KEY=serviceAccountKey.json
~~~

Or provide the credentials JSON through:

~~~env
FIREBASE_CREDENTIALS_JSON={"type":"service_account", "...":"..."}
~~~

## 3. Firestore collections

~~~text
book_bibles/{novel_id}
translation_jobs/{job_id}
~~~

StorageRepository reads from and writes to Firestore first when Firebase is configured. If Firebase is not configured or temporarily unavailable, the in-memory fallback keeps local CLI and development usable.

## 4. Run the backend

~~~bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
~~~

Check:

~~~text
GET http://127.0.0.1:8000/
~~~

## 5. Current limitations

- Firestore stores shared data across devices.
- EPUB/TXT and translated files exist only on the machine running the backend.
- asyncio.create_task() is still an in-process background job. A process restart can interrupt a translation.
- Android must reach the running backend to upload and translate. Firestore does not replace the translation worker.
