(function () {
    'use strict';

    var modal = null;
    var backdrop = null;
    var titleEl = null;
    var textarea = null;
    var errorEl = null;
    var saveBtn = null;
    var cancelBtn = null;
    var activeBtn = null;
    var closing = false;

    function getCsrfToken() {
        var input = document.querySelector('[name=csrfmiddlewaretoken]');
        if (input && input.value) return input.value;
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function setError(message) {
        if (!errorEl) return;
        if (message) {
            errorEl.textContent = message;
            errorEl.hidden = false;
        } else {
            errorEl.textContent = '';
            errorEl.hidden = true;
        }
    }

    function updateNoteButton(btn, note) {
        if (!btn) return;
        btn.setAttribute('data-current-note', note);
        var hasNote = note.trim().length > 0;
        btn.title = hasNote ? 'Edit note' : 'Add note';
        var icon = btn.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = hasNote ? 'sticky_note_2' : 'edit_note';
    }

    function finishClose() {
        if (!modal) return;
        modal.classList.remove('run-note-modal--open', 'run-note-modal--closing');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        activeBtn = null;
        closing = false;
        if (textarea) textarea.value = '';
        setError('');
        if (saveBtn) saveBtn.disabled = false;
        if (cancelBtn) cancelBtn.disabled = false;
    }

    function closeModal() {
        if (!modal || !modal.classList.contains('run-note-modal--open') || closing) return;
        closing = true;
        modal.classList.add('run-note-modal--closing');
        modal.classList.remove('run-note-modal--open');
        window.setTimeout(finishClose, 230);
    }

    function openModal(btn) {
        if (!modal || !textarea || !titleEl) return;
        activeBtn = btn;
        var existing = btn.getAttribute('data-current-note') || '';
        textarea.value = existing;
        titleEl.textContent = existing.trim() ? 'Edit Note' : 'Add Note';
        setError('');
        modal.classList.remove('run-note-modal--closing');
        modal.classList.add('run-note-modal--open');
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        window.requestAnimationFrame(function () {
            textarea.focus();
            textarea.setSelectionRange(textarea.value.length, textarea.value.length);
        });
    }

    function saveNote() {
        if (!activeBtn || !saveBtn) return;
        var note = textarea.value.trim();
        var url = activeBtn.getAttribute('data-note-url');
        if (!url) {
            setError('Could not save note.');
            return;
        }
        var csrf = getCsrfToken();
        var body = new URLSearchParams();
        body.set('note', note);
        if (csrf) body.set('csrfmiddlewaretoken', csrf);

        saveBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        setError('');

        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': csrf,
            },
            body: body.toString(),
            credentials: 'same-origin',
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    updateNoteButton(activeBtn, data.note != null ? data.note : note);
                    closeModal();
                    return;
                }
                setError(data.error || 'Could not save note.');
                saveBtn.disabled = false;
                if (cancelBtn) cancelBtn.disabled = false;
            })
            .catch(function () {
                setError('Could not save note. Please try again.');
                saveBtn.disabled = false;
                if (cancelBtn) cancelBtn.disabled = false;
            });
    }

    function init() {
        modal = document.getElementById('runNoteModal');
        if (!modal) return;

        backdrop = modal.querySelector('[data-run-note-dismiss]');
        titleEl = document.getElementById('runNoteModalTitle');
        textarea = document.getElementById('runNoteModalTextarea');
        errorEl = document.getElementById('runNoteModalError');
        saveBtn = document.getElementById('runNoteModalSave');
        cancelBtn = document.getElementById('runNoteModalCancel');

        modal.querySelectorAll('[data-run-note-dismiss]').forEach(function (el) {
            el.addEventListener('click', closeModal);
        });

        if (saveBtn) {
            saveBtn.addEventListener('click', saveNote);
        }

        if (textarea) {
            textarea.addEventListener('keydown', function (e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    saveNote();
                }
            });
        }

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && modal.classList.contains('run-note-modal--open')) {
                e.preventDefault();
                closeModal();
            }
        });
    }

    window.openRunNoteEditor = function (btn) {
        if (!modal) init();
        openModal(btn);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
