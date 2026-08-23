# Web Reader Design

## Summary

Build a standalone personal web reader at `/reader`, separate from the existing
administration dashboard. The reader is public and read-only, focuses on the
Vietnamese translation, and never exposes untranslated chapters in its catalog.

The first version includes:

- A personal library with cover, title, author, translated chapter count, and
  last-read state.
- A single-column ebook reading mode.
- A translated-chapter table of contents and previous/next navigation.
- Light, dark, and sepia themes.
- Font size, line height, and reading-width controls.
- Browser-local reading progress and preferences.
- Responsive desktop and mobile layouts, touch controls, and keyboard chapter
  navigation.

Bookmarks, notes, full-library search, reader accounts, and cross-device sync
are intentionally out of scope.

## Context

The current dashboard contains a comparison modal that displays original and
translated text side by side. It is useful for administration but is not a
comfortable long-form reading experience. The new reader needs its own public
read contract and interface while leaving translation and library management
flows unchanged.

This is a personal deployment. The expected initial scale is below roughly 100
novels and 2,000 chapters per novel. The design should remain easy to extend if
the collection grows.

## Assumptions

- Reader access does not require a login, PIN, or API token.
- Existing write, translation, deletion, and administration endpoints retain
  their current token requirements.
- Translated chapter content is plain text.
- A chapter is readable only when it is marked completed and translated content
  can actually be retrieved.
- Chapter content is loaded on demand; the full novel is never stored in browser
  local storage.
- The implementation stays within the current FastAPI and static HTML/CSS/JS
  stack. No frontend framework or build pipeline is introduced.
- Reading preferences and progress belong to the current browser only.

## Architecture

Create a new `app/modules/reader/` module with three responsibilities:

- `api.py`: public, read-only HTTP endpoints.
- `service.py`: reader-specific filtering and navigation behavior built on the
  existing `library_service` facade.
- `schemas.py`: stable response models for the public reader contract.

The module must not access SQLAlchemy models, repositories, or storage providers
directly. Library remains the owner of persistence and chapter content. Reader
adapts Library data for a public reading use case.

Expose these endpoints:

- `GET /api/v1/reader/books`
- `GET /api/v1/reader/books/{novel_id}`
- `GET /api/v1/reader/books/{novel_id}/chapters/{chapter_index}`

The list endpoint returns only books with at least one translated chapter. The
detail endpoint returns public metadata and only readable chapters. The chapter
endpoint returns translated content plus previous and next readable chapter
references. Missing, unreadable, or untranslated chapters return `404` and never
fall back to original content.

Add `app/static/reader.html` and serve it from `GET /reader`. The existing root
dashboard remains unchanged.

## Reader Interface

The `/reader` entry screen is the actual personal library, not a landing page.
Book covers are the main visual assets. Each book shows title, author, translated
chapter count, and the last-read chapter when available.

The reading view uses an editorial, long-form reading aesthetic: serif body
typography, restrained ink colors, and a small red accent. Light and sepia themes
use comfortable paper tones; dark mode uses a neutral charcoal background.

Desktop layout:

- Collapsible table-of-contents sidebar.
- Centered reading column with a configurable maximum width.
- Compact top toolbar for navigation and preferences.

Mobile layout:

- Table of contents in a drawer.
- Fixed bottom controls for previous and next chapters.
- Touch-friendly controls without reducing the text area unnecessarily.

A thin top progress bar shows the current scroll position within the chapter.
The interface supports previous/next buttons, chapter selection, and left/right
keyboard navigation. Dynamic chapter text is rendered with DOM text nodes rather
than inserted as HTML.

## Client State and Data Flow

On startup, the client requests the reader book list. Opening a book loads its
metadata and filtered table of contents. Opening a chapter requests only that
chapter. After a successful render, the next readable chapter is prefetched into
a small in-memory cache.

Use these browser-local keys:

- `reader.preferences`: theme, font size, line height, and reading width.
- `reader.progress.{novel_id}`: current chapter, scroll position, and timestamp.

Chapter content is never persisted in `localStorage`. The in-memory chapter cache
is bounded to a few nearby chapters. An `AbortController` cancels obsolete
requests when navigation changes rapidly.

Progress is restored only after chapter content is rendered. Scroll updates are
throttled to avoid excessive storage writes.

## Error Handling and Reliability

The API uses consistent status semantics:

- `404`: book or chapter is absent, untranslated, or unreadable.
- `422`: an identifier or chapter index is invalid.
- `500` or `503`: backend or storage is temporarily unavailable.

The frontend includes loading, empty-library, retry, and unavailable-chapter
states. A failed chapter transition keeps the currently rendered chapter intact.
Missing covers use a styled textual fallback. Long titles and long unbroken text
must not overflow their containers.

The API never logs chapter content or returns original text through Reader
endpoints.

## Performance

The book list and one table of contents per opened book are loaded once per
session. Chapter content is fetched independently. Only the next readable chapter
is prefetched, and the browser cache remains bounded.

The initial contract targets a personal collection. The response schemas should
permit future pagination without coupling the frontend to Library's internal
models.

## Testing Strategy

Backend unit tests cover:

- Excluding books without translated chapters.
- Excluding incomplete chapters from the table of contents.
- Refusing fallback to original content.
- Correct previous/next navigation across chapter index gaps.
- Metadata/content inconsistencies and missing storage objects.

API tests cover response schemas, status codes, Vietnamese Unicode, invalid
identifiers, and the absence of Reader write operations.

Frontend verification covers:

- Empty libraries, missing covers, and long titles.
- Book selection, chapter navigation, table of contents, and library return.
- Preference and progress restoration after reload.
- Light, dark, and sepia themes at desktop and mobile viewports.
- Keyboard and touch interaction.
- HTML/script-like text rendered only as text.
- Failed requests and rapid chapter changes.

Before completion, run the full Python suite, backend compilation, Alembic and
startup smoke checks, JavaScript syntax validation, and desktop/mobile visual
checks.

## Risks

- Existing chapter status can disagree with the translated storage object. The
  chapter endpoint must verify content and fail closed.
- Public read access means anyone who knows the URL can read translated content.
  This is an accepted constraint for the personal deployment.
- Browser-local progress does not follow the user across devices. Cross-device
  sync remains a future feature.
- Very large tables of contents may eventually need server pagination and client
  virtualization.

## Decision Log

1. Use a standalone `/reader` page instead of adding another dashboard tab, to
   keep reading separate from administration.
2. Use a dedicated Reader module and API contract instead of consuming Library
   management DTOs directly, to preserve ownership boundaries.
3. Show only Vietnamese translated chapters and never fall back to source text.
4. Keep reader access public while retaining existing protection on write and
   administration operations.
5. Store preferences and progress in the browser because this is a personal,
   single-user deployment without account infrastructure.
6. Use the existing static frontend stack to keep deployment and maintenance
   simple.
7. Defer bookmarks, notes, search, accounts, and synchronization to avoid growing
   the first version beyond the daily reading workflow.

## Acceptance Criteria

- `/reader` works independently from the dashboard.
- Only books and chapters with readable Vietnamese translations are exposed.
- Chapter transitions are responsive and preserve the current chapter on error.
- Preferences and per-book progress restore correctly after reload.
- Desktop and mobile layouts remain usable without overlap or text overflow.
- Reader changes do not regress existing Library, translation, or administration
  behavior.
