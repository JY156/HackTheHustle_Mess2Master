document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');
    const form = document.getElementById('uploadForm');
    const submitBtn = document.getElementById('submitBtn');
    const submitStatus = document.getElementById('submitStatus');
    const semStartInput = form.querySelector('input[name="sem_start"]');
    const semEndInput = form.querySelector('input[name="sem_end"]');
    const projectsList = document.getElementById('projectsList');
    const selectedFiles = new Map();
    const MAX_FILES = 10;

    // Prevent "nothing happened" UX when required date fields are left blank.
    if (semStartInput && !semStartInput.value) semStartInput.value = '2026-01-12';
    if (semEndInput && !semEndInput.value) semEndInput.value = '2026-05-15';

    // Drag & Drop UI
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = 'var(--primary)'; });
    dropZone.addEventListener('dragleave', () => { dropZone.style.borderColor = 'var(--border)'; });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--border)';
        addFiles(e.dataTransfer.files);
    });
    fileInput.addEventListener('change', () => {
        addFiles(fileInput.files);
        // Allow re-selecting the same filename in subsequent picker opens.
        fileInput.value = '';
    });

    function syncFileInput() {
        const dataTransfer = new DataTransfer();
        selectedFiles.forEach((file) => dataTransfer.items.add(file));
        fileInput.files = dataTransfer.files;
    }

    function addFiles(filesLike) {
        let skippedCount = 0;
        Array.from(filesLike || []).forEach((file) => {
            const key = `${file.name}-${file.size}-${file.lastModified}`;
            if (selectedFiles.has(key)) return;
            if (selectedFiles.size >= MAX_FILES) {
                skippedCount += 1;
                return;
            }
            selectedFiles.set(key, file);
        });
        syncFileInput();
        updateFileList();

        if (skippedCount > 0) {
            setSubmitStatus(
                `You can upload up to ${MAX_FILES} files at once. ${skippedCount} file${skippedCount > 1 ? 's were' : ' was'} not added.`,
                'error'
            );
        }
    }

    function removeFile(fileKey) {
        selectedFiles.delete(fileKey);
        syncFileInput();
        updateFileList();
    }

    function updateFileList() {
        const files = Array.from(selectedFiles.values());
        fileList.innerHTML = '';

        if (!files.length) {
            fileList.textContent = `Supports PDF, DOC, DOCX, TXT, MP3, WAV, M4A, OGG · up to ${MAX_FILES} files`;
            return;
        }

        selectedFiles.forEach((file, key) => {
            const chip = document.createElement('span');
            chip.className = 'file-chip';

            const name = document.createElement('span');
            name.className = 'file-chip-name';
            name.textContent = file.name;

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'file-chip-remove';
            removeBtn.setAttribute('aria-label', `Remove ${file.name}`);
            removeBtn.title = `Remove ${file.name}`;
            removeBtn.textContent = 'x';
            removeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                removeFile(key);
            });

            chip.appendChild(name);
            chip.appendChild(removeBtn);
            fileList.appendChild(chip);
        });
    }

    function setSubmitStatus(message, type = '') {
        if (!submitStatus) return;
        submitStatus.textContent = message;
        submitStatus.className = 'submit-status';
        if (type) submitStatus.classList.add(type);
    }

    // Show a one-time success message after reload.
    const pendingStatus = window.sessionStorage.getItem('submitStatusMessage');
    const shouldScrollToResults = window.sessionStorage.getItem('scrollToResults') === '1';
    if (pendingStatus) {
        setSubmitStatus(pendingStatus, 'success');
        window.sessionStorage.removeItem('submitStatusMessage');
    }
    if (shouldScrollToResults && projectsList) {
        projectsList.scrollIntoView({ behavior: 'smooth', block: 'start' });
        projectsList.classList.add('just-updated');
        setTimeout(() => projectsList.classList.remove('just-updated'), 1800);
        window.sessionStorage.removeItem('scrollToResults');
    }

    // Form Submit
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        if (!form.checkValidity()) {
            form.reportValidity();
            setSubmitStatus('Please fill all required fields.', 'error');
            return;
        }

        if (!fileInput.files.length) {
            setSubmitStatus('Please add at least one file before generating.', 'error');
            return;
        }

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing AI...';
        setSubmitStatus('Uploading data and generating your plan...', 'loading');

        const formData = new FormData(form);
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 45000);
        try {
            const res = await fetch('/process', { method: 'POST', body: formData, signal: controller.signal });
            const data = await res.json().catch(() => ({}));

            if (!res.ok) {
                const message = data.error || `Request failed (${res.status})`;
                throw new Error(message);
            }

            if (data.status === 'success') {
                const receivedNames = Array.isArray(data.received_files) && data.received_files.length
                    ? ` Files: ${data.received_files.map(file => file.name).join(', ')}`
                    : ' No files received.';
                let successMessage = `Plan generated for ${data.project_name || 'your project'}.${receivedNames}`;
                if (data.fallback) {
                    const reason = data.fallback_reason || 'AI fallback triggered';
                    const model = data.used_model || 'unknown model';
                    successMessage = `Fallback result used (${model}). Reason: ${reason}.${receivedNames}`;
                }
                setSubmitStatus(`${successMessage} Refreshing...`, 'success');
                window.sessionStorage.setItem('submitStatusMessage', successMessage);
                window.sessionStorage.setItem('scrollToResults', '1');
                setTimeout(() => window.location.reload(), 1200);
            }
            else throw new Error('Processing completed with unexpected format.');
        } catch (err) {
            console.error(err);
            const message = err.name === 'AbortError'
                ? 'Request timed out after 45s. Try a smaller file or retry.'
                : err.message;
            setSubmitStatus(`Error: ${message}`, 'error');
            alert(`Error processing upload: ${message}`);
        } finally {
            clearTimeout(timeoutId);
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Generate Plan';
        }
    });

    // Calendar Redirect (Google Calendar URL Template) - per-task buttons
    document.querySelectorAll('.btn-calendar-icon').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const title = encodeURIComponent(btn.dataset.title || 'Mess2Master Task');
            const date = btn.dataset.date || new Date().toISOString().split('T')[0].replace(/-/g, '');
            const formatted = date.replace(/-/g, '');
            const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${formatted}/${formatted}&details=Task%20extracted%20by%20Mess2Master%20AI`;
            window.open(url, '_blank');
        });
    });

    // Notion Sync Button
    const syncNotionBtn = document.getElementById('syncNotionBtn');
    const syncStatus = document.getElementById('syncStatus');

    if (syncNotionBtn) {
        syncNotionBtn.addEventListener('click', async () => {
            syncNotionBtn.disabled = true;
            syncNotionBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Syncing...';
            syncStatus.textContent = 'Syncing tasks to Notion...';
            syncStatus.className = 'sync-status loading';

            try {
                const res = await fetch('/sync-notion', { method: 'POST' });
                const data = await res.json();

                if (res.ok && data.status === 'success') {
                    syncStatus.textContent = `✅ Synced ${data.synced_count}/${data.total_tasks} tasks to Notion`;
                    syncStatus.className = 'sync-status success';
                    if (data.errors.length > 0) {
                        syncStatus.textContent += ` (${data.errors.length} warnings)`;
                    }
                } else {
                    const detailed = Array.isArray(data.errors) && data.errors.length ? data.errors[0] : '';
                    syncStatus.textContent = `❌ Sync failed: ${data.message || data.error || detailed || 'Unknown error'}`;
                    syncStatus.className = 'sync-status error';
                }
            } catch (err) {
                console.error(err);
                syncStatus.textContent = `❌ Error: ${err.message}`;
                syncStatus.className = 'sync-status error';
            } finally {
                syncNotionBtn.disabled = false;
                syncNotionBtn.innerHTML = '<i class="fas fa-database"></i> Sync to Notion';
            }
        });
    }
});