// snapadmin/static/snapadmin/js/offline.js

(function() {
    const DB_NAME = 'SnapAdminDB';
    const DB_VERSION = 1;
    let db;

    function initDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains('pending_changes')) {
                    db.createObjectStore('pending_changes', { keyPath: 'id', autoIncrement: true });
                }
                if (!db.objectStoreNames.contains('model_data')) {
                    db.createObjectStore('model_data', { keyPath: 'pk' });
                }
            };
            request.onsuccess = (event) => {
                db = event.target.result;
                resolve(db);
            };
            request.onerror = (event) => reject(event.target.error);
        });
    }

    async function registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            try {
                // Register from the root-relative URL provided by our view
                // to ensure the scope covers /admin/
                await navigator.serviceWorker.register('/api/sw.js', { scope: '/' });
                console.log('SnapAdmin Service Worker registered');
            } catch (error) {
                console.error('Service Worker registration failed:', error);
            }
        }
    }

    async function syncChanges() {
        if (!navigator.onLine || !db) return;

        const transaction = db.transaction(['pending_changes'], 'readwrite');
        const store = transaction.objectStore('pending_changes');
        const request = store.getAll();

        request.onsuccess = async () => {
            const changes = request.result;
            if (changes.length === 0) return;

            console.log('Attempting to sync ' + changes.length + ' pending changes...');

            for (const change of changes) {
                try {
                    const response = await fetch(change.url, {
                        method: change.method,
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-CSRFToken': getCookie('csrftoken')
                        },
                        body: change.body
                    });
                    if (response.ok) {
                        const delTx = db.transaction(['pending_changes'], 'readwrite');
                        delTx.objectStore('pending_changes').delete(change.id);
                        console.log('Synced change:', change);
                    }
                } catch (error) {
                    console.error('Sync failed for change:', change, error);
                }
            }
        };
    }

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // Intercept form submissions when offline
    window.addEventListener('submit', function(e) {
        if (!navigator.onLine) {
            const form = e.target;
            if (form.method.toLowerCase() === 'post') {
                e.preventDefault();
                const formData = new FormData(form);
                const params = new URLSearchParams();
                for (const pair of formData.entries()) {
                    params.append(pair[0], pair[1]);
                }

                const change = {
                    url: form.action || window.location.href,
                    method: 'POST',
                    body: params.toString(),
                    timestamp: new Date().getTime()
                };

                const tx = db.transaction(['pending_changes'], 'readwrite');
                tx.objectStore('pending_changes').add(change);

                alert('Connection lost. Change saved locally and will be synced when you are back online.');

                // Redirect back to changelist if possible
                const parts = window.location.pathname.split('/');
                if (parts.length > 3) {
                    window.location.href = parts.slice(0, -2).join('/') + '/';
                }
            }
        }
    });

    window.addEventListener('online', syncChanges);

    document.addEventListener('DOMContentLoaded', async () => {
        await initDB();
        await registerServiceWorker();
        if (navigator.onLine) {
            await syncChanges();
        }
    });

})();
