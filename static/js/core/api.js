// ============================================================
// Core - Thin fetch wrappers that unify error handling
// Callers keep their per-call toast messages in place and just
// await these helpers; any non-2xx response or `{error: ...}`
// payload is raised as an Error with a useful message.
// ============================================================

async function apiFetch(url, options) {
    var opts = options || {};
    var resp = await fetch(url, opts);
    var data = null;
    try {
        data = await resp.json();
    } catch (parseErr) {
        data = null;
    }
    if (!resp.ok || (data && data.error)) {
        var message = (data && (data.error || data.message))
            || ('Request failed (' + resp.status + ')');
        var err = new Error(message);
        err.response = data;
        err.status = resp.status;
        throw err;
    }
    return data;
}

async function apiPost(url, body) {
    return apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
    });
}

async function apiPatch(url, body) {
    return apiFetch(url, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
    });
}

async function apiPostForm(url, formData) {
    return apiFetch(url, { method: 'POST', body: formData });
}

async function apiGet(url) {
    return apiFetch(url);
}
