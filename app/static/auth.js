(function () {
    "use strict";

    const rawFetch = window.fetch.bind(window);
    let clientPromise;
    let redirecting = false;
    const LOCAL_SIGNED_OUT_KEY = "epub.local-auth.signed-out";

    function localSignedOut() {
        try { return sessionStorage.getItem(LOCAL_SIGNED_OUT_KEY) === "1"; } catch (_) { return false; }
    }

    function setLocalSignedOut(value) {
        try {
            if (value) sessionStorage.setItem(LOCAL_SIGNED_OUT_KEY, "1");
            else sessionStorage.removeItem(LOCAL_SIGNED_OUT_KEY);
        } catch (_) { /* Storage can be unavailable in private browsing. */ }
    }

    function localSession() {
        if (localSignedOut()) return null;
        return {
            access_token: null,
            token_type: "bearer",
            user: { id: "local-development-user", email: "local@localhost" }
        };
    }

    function createLocalClient() {
        return {
            auth: {
                async getSession() { return { data: { session: localSession() }, error: null }; },
                async refreshSession() { return { data: { session: localSession() }, error: null }; },
                async signInWithPassword() {
                    setLocalSignedOut(false);
                    return { data: { session: localSession() }, error: null };
                },
                async signOut() { setLocalSignedOut(true); return { error: null }; },
                onAuthStateChange() { return { data: { subscription: { unsubscribe() {} } } }; }
            }
        };
    }

    function nextPath(value) {
        const candidate = String(value || "").trim();
        return candidate === "/" || candidate === "/reader" ? candidate : "/reader";
    }

    async function getClient() {
        if (!clientPromise) {
            clientPromise = (async function () {
                const response = await rawFetch("/api/auth/config", { headers: { Accept: "application/json" } });
                const config = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(config.detail || "Xác thực chưa được cấu hình trên backend.");
                }
                if (config.mode === "local") {
                    return createLocalClient();
                }
                if (config.mode !== "supabase" || !config.supabase_url || !config.supabase_publishable_key) {
                    throw new Error("Backend trả về cấu hình xác thực không hợp lệ.");
                }
                if (!window.supabase || typeof window.supabase.createClient !== "function") {
                    throw new Error("Không tải được mô-đun xác thực.");
                }
                const client = window.supabase.createClient(config.supabase_url, config.supabase_publishable_key, {
                    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
                });
                client.auth.onAuthStateChange(function () {});
                return client;
            })();
        }
        return clientPromise;
    }

    async function getSession() {
        const client = await getClient();
        const result = await client.auth.getSession();
        if (result.error) throw result.error;
        return result.data.session;
    }

    async function getAccessToken() {
        const session = await getSession();
        return session && session.access_token ? session.access_token : null;
    }

    async function refreshSession() {
        const client = await getClient();
        const result = await client.auth.refreshSession();
        if (result.error) throw result.error;
        return result.data.session;
    }

    async function signIn(email, password) {
        const client = await getClient();
        return client.auth.signInWithPassword({ email: String(email || "").trim(), password });
    }

    async function signOut() {
        try {
            const client = await getClient();
            await client.auth.signOut();
        } finally {
            redirecting = false;
        }
    }

    async function guard() {
        try {
            const session = await getSession();
            if (session) return session;
        } catch (_) {
            // Login page displays the configuration error; protected pages use
            // the same safe redirect as an expired session.
        }
        if (!redirecting) {
            redirecting = true;
            const next = encodeURIComponent(window.location.pathname === "/" ? "/" : "/reader");
            window.location.replace("/login?next=" + next);
        }
        return null;
    }

    async function authFetch(input, init, retry) {
        const options = Object.assign({}, init || {});
        const headers = new Headers(options.headers || {});
        const url = typeof input === "string" ? input : input.url;
        const absolute = new URL(url, window.location.origin);
        if (absolute.origin === window.location.origin && absolute.pathname.startsWith("/api/v1/")) {
            const token = await getAccessToken();
            if (token) headers.set("Authorization", "Bearer " + token);
        }
        options.headers = headers;
        let response = await rawFetch(input, options);
        if (response.status === 401 && !retry) {
            try {
                const refreshed = await refreshSession();
                if (refreshed && refreshed.access_token) return authFetch(input, init, true);
            } catch (_) { /* fall through to sign out */ }
            await signOut();
            if (!redirecting) {
                redirecting = true;
                window.location.replace("/login?next=" + encodeURIComponent(window.location.pathname === "/" ? "/" : "/reader"));
            }
        }
        return response;
    }

    async function migrateReaderState() {
        const stateResponse = await authFetch("/api/v1/reader/me/state");
        if (!stateResponse.ok) return null;
        const state = await stateResponse.json();
        if (state.local_migrated_at) return state;
        const progress = [];
        for (let index = 0; index < localStorage.length; index += 1) {
            const key = localStorage.key(index) || "";
            if (!key.startsWith("reader.progress.")) continue;
            try {
                const value = JSON.parse(localStorage.getItem(key) || "null");
                const novelId = key.slice("reader.progress.".length);
                if (value && Number.isInteger(value.chapterIndex) && novelId) {
                    progress.push({ novel_id: novelId, chapter_index: value.chapterIndex, scroll_top: Math.max(0, Number(value.scrollTop) || 0) });
                }
            } catch (_) { /* Ignore malformed legacy entries. */ }
        }
        let preferences = {};
        try { preferences = JSON.parse(localStorage.getItem("reader.preferences") || "{}"); } catch (_) {}
        const response = await authFetch("/api/v1/reader/me/migrate-local", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ preferences, progress })
        });
        return response.ok ? response.json() : state;
    }

    window.EpubAuth = {
        ready: getClient(),
        getClient,
        getSession,
        getAccessToken,
        refreshSession,
        signIn,
        signOut,
        guard,
        authFetch,
        migrateReaderState,
        nextPath
    };

    window.fetch = function (input, init) {
        const url = typeof input === "string" ? input : input && input.url;
        try {
            const absolute = new URL(url, window.location.origin);
            if (absolute.origin === window.location.origin && absolute.pathname.startsWith("/api/v1/")) {
                return authFetch(input, init, false);
            }
        } catch (_) { /* Let the native fetch handle unusual Request objects. */ }
        return rawFetch(input, init);
    };
})();