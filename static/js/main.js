document.addEventListener('DOMContentLoaded', async () => {
    // === DOM Elements ===
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');
    const form = document.getElementById('uploadForm');
    const submitBtn = document.getElementById('submitBtn');
    const submitStatus = document.getElementById('submitStatus');
    const projectNameInput = document.getElementById('projectNameInput');
    const projectSelect = document.getElementById('projectSelect');
    const newProjectInput = document.getElementById('newProjectInput');
    const projectHint = document.getElementById('projectHint');
    const semesterBanner = document.getElementById('semesterBanner');
    const saveSemesterBtn = document.getElementById('saveSemesterBtn');
    const resetSemesterBtn = document.getElementById('resetSemesterBtn');
    const projectsList = document.getElementById('projectsList');
    const syncNotionBtn = document.getElementById('syncNotionBtn');
    const syncStatus = document.getElementById('syncStatus');

    const selectedFiles = new Map();
    const MAX_FILES = 10;

    // === 1. Check Semester Status on Load ===
    async function checkSemester() {
        try {
            const res = await fetch('/api/semester/status');
            const data = await res.json();
            if (semesterBanner) {
                semesterBanner.style.display = data.configured ? 'none' : 'block';
            }
        } catch (err) {
            console.warn('Semester check failed:', err);
            if (semesterBanner) semesterBanner.style.display = 'block';
        }
    }
    checkSemester();

    // === 2. Save Semester Settings ===
    saveSemesterBtn?.addEventListener('click', async () => {
        const start = document.getElementById('semStart')?.value;
        const end = document.getElementById('semEnd')?.value;
        const breakWeek = document.getElementById('breakWeek')?.value || 8;
        
        if (!start || !end) {
            alert('Please select both semester start and end dates');
            return;
        }
        
        try {
            const res = await fetch('/api/semester', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({start, end, break_week: breakWeek})
            });
            const data = await res.json();
            if (data.status === 'success') {
                semesterBanner.style.display = 'none';
                alert('✅ Semester settings saved!');
            }
        } catch (err) {
            console.error('Save semester failed:', err);
            alert('Failed to save semester settings');
        }
    });

    // === 3. Reset Semester ===
    resetSemesterBtn?.addEventListener('click', async () => {
        if (!confirm('⚠️ This will delete all projects and tasks. Start fresh?')) return;
        try {
            const res = await fetch('/api/reset', {method: 'POST'});
            const data = await res.json();
            if (data.status === 'reset') window.location.reload();
        } catch (err) {
            console.error('Reset failed:', err);
            alert('Failed to reset semester');
        }
    });

    // === 4. File Management (Main's robust chip system) ===
    function syncFileInput() {
        const dataTransfer = new DataTransfer();
        selectedFiles.forEach((file) => dataTransfer.items.add(file));
        if (fileInput) fileInput.files = dataTransfer.files;
    }

    function addFiles(filesLike) {
        let skippedCount = 0;
        Array.from(filesLike || []).forEach((file) => {
            const key = `${file.name}-${file.size}-${file.lastModified}`;
            if (selectedFiles.has(key)) return;
            if (selectedFiles.size >= MAX_FILES) { skippedCount += 1; return; }
            selectedFiles.set(key, file);
        });
        syncFileInput();
        updateFileList();
        if (skippedCount > 0) {
            setSubmitStatus(`You can upload up to ${MAX_FILES} files at once. ${skippedCount} file${skippedCount > 1 ? 's were' : ' was'} not added.`, 'error');
        }
    }

    function removeFile(fileKey) {
        selectedFiles.delete(fileKey);
        syncFileInput();
        updateFileList();
    }

    function updateFileList() {
        if (!fileList) return;
        const files = Array.from(selectedFiles.values());
        fileList.innerHTML = '';

        if (!files.length) {
            fileList.textContent = `Supports PDF, DOC, DOCX, TXT, MP3, WAV, M4A, OGG · up to ${MAX_FILES} files`;
            if (dropZone) dropZone.classList.remove('multiple-files');
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
            removeBtn.textContent = 'x';
            removeBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); removeFile(key); });
            chip.appendChild(name);
            chip.appendChild(removeBtn);
            fileList.appendChild(chip);
        });

        if (dropZone) {
            dropZone.classList.toggle('multiple-files', files.length > 1);
        }
    }

    // Drag & Drop
    dropZone?.addEventListener('click', () => fileInput?.click());
    dropZone?.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = 'var(--primary)'; });
    dropZone?.addEventListener('dragleave', () => { dropZone.style.borderColor = 'var(--border)'; });
    dropZone?.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--border)';
        if (fileInput) { addFiles(e.dataTransfer.files); fileInput.value = ''; }
    });
    fileInput?.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value = ''; });

    // === 5. Status Helpers ===
    function setSubmitStatus(message, type = '') {
        if (!submitStatus) return;
        submitStatus.textContent = message;
        submitStatus.className = 'submit-status';
        if (type) submitStatus.classList.add(type);
    }

    function setNotionSuccessStatus(statusEl, syncedCount, dashboardUrl) {
        if (!statusEl) return;
        statusEl.className = 'sync-status success';
        const safeUrl = (dashboardUrl || '').trim();
        if (/^https?:\/\//i.test(safeUrl)) {
            statusEl.innerHTML = `Successfully synced! <a href="${safeUrl}" target="_blank" rel="noopener noreferrer">View your Notion Dashboard ↗</a>`;
            return;
        }
        statusEl.textContent = `✅ Synced ${syncedCount || 'all'} tasks to Notion`;
    }

    // === AI Guidance Modal ===
    const guidanceModal = document.getElementById('guidanceModal');
    const guidanceBody = document.getElementById('guidanceBody');
    const guidanceTaskTitle = document.getElementById('guidanceTaskTitle');
    const closeGuidanceModal = document.getElementById('closeGuidanceModal');

    function showGuidanceModal(title, bodyText) {
        if (!guidanceModal || !guidanceBody || !guidanceTaskTitle) return;
        guidanceTaskTitle.textContent = title ? `Task: ${title}` : '';
        guidanceBody.textContent = bodyText || 'No guidance available.';
        guidanceModal.style.display = 'flex';
    }

    function hideGuidanceModal() {
        if (guidanceModal) guidanceModal.style.display = 'none';
    }

    closeGuidanceModal?.addEventListener('click', hideGuidanceModal);
    guidanceModal?.addEventListener('click', (e) => {
        if (e.target === guidanceModal) hideGuidanceModal();
    });

    document.querySelectorAll('[data-guidance-task]').forEach((button) => {
        button.addEventListener('click', async () => {
            const projectName = button.dataset.project;
            const taskId = button.dataset.taskId;
            const taskTitle = button.dataset.taskTitle || 'Task';
            if (!projectName || !taskId) return;

            const originalHtml = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

            try {
                const res = await fetch('/api/tasks/guidance', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ project_name: projectName, task_id: taskId }),
                });
                const data = await res.json();
                if (!res.ok || data.status !== 'success') {
                    throw new Error(data.error || 'Unable to generate guidance');
                }
                showGuidanceModal(taskTitle, data.guidance || 'No guidance available.');
            } catch (err) {
                console.error(err);
                showGuidanceModal(taskTitle, `Unable to generate guidance right now.\n${err.message}`);
            } finally {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        });
    });

    function resolveSelectedProjectName() {
        const unifiedProjectVal = projectNameInput?.value?.trim();
        const newProjectVal = newProjectInput?.value?.trim();
        const existingProjectVal = projectSelect?.value;
        return unifiedProjectVal || newProjectVal || existingProjectVal || '';
    }

    function focusAlertTaskFromHash() {
        const hash = window.location.hash || '';
        if (!hash.startsWith('#task-')) return;
        const target = document.querySelector(hash);
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.add('alert-focus');
        setTimeout(() => target.classList.remove('alert-focus'), 2600);
    }

    document.querySelectorAll('.complete-toggle').forEach((checkbox) => {
        checkbox.addEventListener('change', async (e) => {
            const taskEl = e.target.closest('.task-item');
            const taskId = taskEl?.dataset.id;
            const projectName = taskEl?.dataset.project;
            const titleEl = taskEl?.querySelector('.task-title');
            if (!taskEl || !taskId || !projectName) return;

            try {
                const res = await fetch('/api/tasks/complete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_id: taskId, project_name: projectName }),
                });
                const data = await res.json();
                if (!res.ok || !['completed', 'pending'].includes(data.status)) {
                    throw new Error(data.error || 'Unable to update task state');
                }
                const done = data.status === 'completed';
                taskEl.classList.toggle('completed', done);
                if (titleEl) titleEl.style.textDecoration = done ? 'line-through' : 'none';
            } catch (err) {
                console.error(err);
                checkbox.checked = !checkbox.checked;
            }
        });
    });

    // Restore status after reload
    const pendingStatus = window.sessionStorage.getItem('submitStatusMessage');
    const highlightSlug = window.sessionStorage.getItem('highlightProjectSlug');
    if (pendingStatus) {
        setSubmitStatus(pendingStatus, 'success');
        window.sessionStorage.removeItem('submitStatusMessage');
    }
    if (highlightSlug && projectsList) {
        const target = document.getElementById(highlightSlug) || projectsList;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.classList.add('just-updated');
        setTimeout(() => target.classList.remove('just-updated'), 1800);
        window.sessionStorage.removeItem('highlightProjectSlug');
    }
    focusAlertTaskFromHash();

    function slugifyProjectName(name) {
        return (name || '').trim().replace(/\s+/g, '-');
    }

    // === Form Submit (Stay on Index, Show Updated Project) ===
    form?.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); setSubmitStatus('Please fill all required fields.', 'error'); return; }

        // 🔑 Smart project name resolution: New Input > Dropdown
        const unifiedProjectVal = projectNameInput?.value.trim();
        const newProjectVal = newProjectInput?.value.trim();
        const existingProjectVal = projectSelect?.value;
        let projectName = '';
        if (unifiedProjectVal) { projectName = unifiedProjectVal; }
        else if (newProjectVal) { projectName = newProjectVal; }
        else if (existingProjectVal) { projectName = existingProjectVal; }

        if (!projectName) {
            alert('⚠️ Please select an existing project OR type a new project name');
            return;
        }

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing AI...';
        setSubmitStatus('Uploading data and generating your plan...', 'loading');

        const formData = new FormData(form);
        formData.set('project_name', projectName); // ✅ Force correct value

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 45000);

        try {
            const res = await fetch('/process', { method: 'POST', body: formData, signal: controller.signal });
            const data = await res.json().catch(() => ({}));

            if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);

            if (data.status === 'success') {
                const received = Array.isArray(data.received_files) ? data.received_files.map(f => f.name).join(', ') : '';
                let msg = `✅ Plan updated for ${data.project_name || 'your project'}.`;
                if (data.fallback) msg = `⚠️ Fallback result used (${data.used_model || 'unknown'}). Reason: ${data.fallback_reason || 'AI limit'}.`;
                if (received) msg += ` Files: ${received}`;
                
                setSubmitStatus(`${msg} Refreshing view...`, 'success');
                window.sessionStorage.setItem('highlightProjectSlug', slugifyProjectName(data.project_name || projectName));
                
                // ✅ STAY ON INDEX: Just reload to show updated project
                setTimeout(() => window.location.reload(), 1200);
            } else {
                throw new Error('Processing completed with unexpected format.');
            }
        } catch (err) {
            console.error(err);
            const msg = err.name === 'AbortError' ? 'Request timed out after 45s. Try a smaller file.' : err.message;
            setSubmitStatus(`Error: ${msg}`, 'error');
            alert(`Error processing upload: ${msg}`);
        } finally {
            clearTimeout(timeoutId);
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Generate Plan';
        }
    });

    // === 7. Calendar Redirect (per-task buttons) ===
    document.querySelectorAll('.btn-calendar-icon').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const title = encodeURIComponent(btn.dataset.title || 'Mess2Master Task');
            const date = (btn.dataset.date || new Date().toISOString().split('T')[0]).replace(/-/g, '');
            const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${date}/${date}&details=Task%20extracted%20by%20Mess2Master%20AI`;
            window.open(url, '_blank');
        });
    });

    // === 8. Notion Sync (with fallback) ===
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
                    setNotionSuccessStatus(syncStatus, data.synced_count, data.dashboard_url);
                } else {
                    // Fallback: copy markdown to clipboard
                    if (data.clipboard_markdown) {
                        await navigator.clipboard.writeText(data.clipboard_markdown);
                        syncStatus.textContent = '✅ Notion API unavailable. Checklist copied to clipboard!';
                        syncStatus.className = 'sync-status success';
                        alert('📋 Markdown checklist copied! Open Notion → New Page → Ctrl+V');
                    } else {
                        syncStatus.textContent = `❌ Sync failed: ${data.message || data.error || 'Unknown error'}`;
                        syncStatus.className = 'sync-status error';
                    }
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

    // === Notion Sync (Multiple Buttons) ===
    document.querySelectorAll('[id^="syncNotionBtn-"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const btnId = btn.id.split('-')[1];
            const statusEl = document.getElementById(`syncStatus-${btnId}`);
            const projectName = btn.dataset.project;
            
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Syncing...';
            statusEl.textContent = 'Syncing tasks to Notion...';
            statusEl.className = 'sync-status loading';

            try {
                const res = await fetch('/sync-notion', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                // 🔍 Check if response is actually JSON
                const contentType = res.headers.get("content-type");
                if (!contentType || !contentType.includes("application/json")) {
                    const htmlError = await res.text();
                    console.error("❌ Server returned HTML instead of JSON:", htmlError.substring(0, 300));
                    throw new Error("Server returned an error page. Check console for details.");
                }
                
                const data = await res.json();

                if (res.ok && data.status === 'success') {
                    setNotionSuccessStatus(statusEl, data.synced_count, data.dashboard_url);
                } else {
                    // Fallback: copy markdown to clipboard
                    if (data.clipboard_markdown) {
                        await navigator.clipboard.writeText(data.clipboard_markdown);
                        statusEl.textContent = '✅ Notion API unavailable. Checklist copied to clipboard!';
                        statusEl.className = 'sync-status success';
                        alert(`📋 Markdown checklist for "${projectName}" copied!\n\nOpen Notion → New Page → Ctrl+V`);
                    } else {
                        statusEl.textContent = `❌ Sync failed: ${data.message || data.error || 'Unknown error'}`;
                        statusEl.className = 'sync-status error';
                    }
                }
            } catch (err) {
                console.error(err);
                statusEl.textContent = `❌ Error: ${err.message}`;
                statusEl.className = 'sync-status error';
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-database"></i> Sync to Notion';
            }
        });
    });

    // === Editable Task Cards ===
    document.querySelectorAll('[data-edit-toggle]').forEach((button) => {
        button.addEventListener('click', () => {
            const taskItem = button.closest('.task-item');
            const viewEl = taskItem?.querySelector('.task-view');
            const formEl = taskItem?.querySelector('.task-edit-form');
            if (!taskItem || !viewEl || !formEl) return;
            taskItem.classList.add('is-editing');
            viewEl.hidden = true;
            formEl.hidden = false;
            const firstField = formEl.querySelector('input, select, textarea');
            firstField?.focus();
        });
    });

    document.querySelectorAll('[data-cancel-edit]').forEach((button) => {
        button.addEventListener('click', () => {
            const taskItem = button.closest('.task-item');
            const viewEl = taskItem?.querySelector('.task-view');
            const formEl = taskItem?.querySelector('.task-edit-form');
            if (!taskItem || !viewEl || !formEl) return;
            taskItem.classList.remove('is-editing');
            formEl.hidden = true;
            viewEl.hidden = false;
        });
    });

    document.querySelectorAll('.task-edit-form').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();

            const projectName = form.dataset.project;
            const taskId = form.dataset.taskId;
            const statusEl = form.querySelector('.task-save-status');
            const saveBtn = form.querySelector('.task-save-btn');
            const formData = new FormData(form);

            if (!projectName || !taskId) return;

            saveBtn.disabled = true;
            if (statusEl) statusEl.textContent = 'Saving...';

            try {
                const res = await fetch('/api/tasks/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        project_name: projectName,
                        task_id: taskId,
                        title: formData.get('title') || '',
                        deadline: formData.get('deadline') || '',
                        owner: formData.get('owner') || '',
                        priority: formData.get('priority') || 'medium',
                    }),
                });
                const data = await res.json();

                if (!res.ok || data.status !== 'success') {
                    throw new Error(data.error || 'Unable to save task');
                }

                if (statusEl) statusEl.textContent = 'Saved';
                setTimeout(() => window.location.reload(), 400);
            } catch (err) {
                console.error(err);
                if (statusEl) statusEl.textContent = `Error: ${err.message}`;
            } finally {
                saveBtn.disabled = false;
            }
        });
    });

    document.querySelectorAll('[data-delete-task]').forEach((button) => {
        button.addEventListener('click', async () => {
            const taskItem = button.closest('.task-item');
            const form = taskItem?.querySelector('.task-edit-form');
            const statusEl = form?.querySelector('.task-save-status');
            const projectName = form?.dataset.project;
            const taskId = form?.dataset.taskId;

            if (!form || !projectName || !taskId) return;
            if (!confirm('Delete this task?')) return;

            button.disabled = true;
            if (statusEl) statusEl.textContent = 'Deleting...';

            try {
                const res = await fetch('/api/tasks/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        project_name: projectName,
                        task_id: taskId,
                    }),
                });
                const data = await res.json();

                if (!res.ok || data.status !== 'deleted') {
                    throw new Error(data.error || 'Unable to delete task');
                }

                if (statusEl) statusEl.textContent = 'Deleted';
                setTimeout(() => window.location.reload(), 300);
            } catch (err) {
                console.error(err);
                if (statusEl) statusEl.textContent = `Error: ${err.message}`;
            } finally {
                button.disabled = false;
            }
        });
    });

    // === Voice Feature with Privacy Flow ===
    const privacyModal = document.getElementById('privacyModal');
    const startVoiceBtn = document.getElementById('startVoiceBtn');
    const stopVoiceBtn = document.getElementById('stopVoiceBtn');
    const agreePrivacy = document.getElementById('agreePrivacy');
    const cancelPrivacy = document.getElementById('cancelPrivacy');
    const voiceStatus = document.getElementById('voiceStatus');
    const liveTranscript = document.getElementById('liveTranscript');

    let recognition = null;
    let isRecording = false;
    let fullTranscript = '';
    let hasAgreedPrivacy = sessionStorage.getItem('voicePrivacyAgreed') === 'true';

    // 1. Privacy Modal Handlers
    if (startVoiceBtn && privacyModal) {
    startVoiceBtn.addEventListener('click', () => {
        const selectedProject = resolveSelectedProjectName();
        if (!selectedProject) {
        voiceStatus.innerHTML = '⚠️ Please choose a project first before starting voice meeting.';
        projectNameInput?.focus();
        return;
        }
        if (!hasAgreedPrivacy) {
        privacyModal.style.display = 'flex';
        } else {
        startRecording();
        }
    });
    }

    if (agreePrivacy) {
    agreePrivacy.addEventListener('click', () => {
        hasAgreedPrivacy = true;
        sessionStorage.setItem('voicePrivacyAgreed', 'true');
        privacyModal.style.display = 'none';
        startRecording();
    });
    }

    if (cancelPrivacy) {
    cancelPrivacy.addEventListener('click', () => privacyModal.style.display = 'none');
    }

    // 2. Web Speech API Setup
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event) => {
        let interim = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
            const sentence = transcript.trim().replace(/[.?!]+$/, '');
            fullTranscript += `${sentence}. `;
        }
        else interim = transcript;
        }
        liveTranscript.innerHTML = `<strong>Transcript:</strong> ${fullTranscript}<br><em style="color: var(--text-muted);">Listening: ${interim}</em>`;
        liveTranscript.scrollTop = liveTranscript.scrollHeight;
    };

    recognition.onerror = (event) => {
        console.error('Speech error:', event.error);
        voiceStatus.innerHTML = `<span style="color: var(--danger);">❌ ${event.error}. Try again.</span>`;
        stopRecordingUI();
    };

    recognition.onend = () => { if (isRecording) recognition.start(); };
    } else {
    voiceStatus.innerHTML = '⚠️ Voice not supported. Please use Chrome or Edge.';
    if (startVoiceBtn) startVoiceBtn.disabled = true;
    }

    // 3. Recording Controls
    function startRecording() {
    if (!recognition) return;
    fullTranscript = '';
    isRecording = true;
    recognition.start();
    startVoiceBtn.style.display = 'none';
    stopVoiceBtn.style.display = 'inline-flex';
    voiceStatus.innerHTML = '<span class="recording-dot"></span> Recording... Speak clearly. AI is listening.';
    liveTranscript.style.display = 'block';
    liveTranscript.innerHTML = '<em>Listening...</em>';
    }

    function stopRecordingUI() {
    isRecording = false;
    stopVoiceBtn.style.display = 'none';
    startVoiceBtn.style.display = 'inline-flex';
    }

    if (stopVoiceBtn) {
    stopVoiceBtn.addEventListener('click', async () => {
        if (!recognition) return;
        recognition.stop();
        stopRecordingUI();
        voiceStatus.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Extracting tasks from conversation...';
        
        if (fullTranscript.trim().length > 30) {
        await processVoiceTranscript(fullTranscript);
        } else {
        voiceStatus.innerHTML = '⚠️ Too short. Try speaking more.';
        liveTranscript.style.display = 'none';
        }
    });
    }

    // 4. Send to Backend
    async function processVoiceTranscript(transcript) {
    try {
        const projectName = resolveSelectedProjectName();
        if (!projectName) {
        voiceStatus.innerHTML = '⚠️ Please choose a project first before processing voice transcript.';
        projectNameInput?.focus();
        return;
        }

        const formData = new FormData();
        formData.append('notes', `Voice meeting transcript:\n${transcript}`);

        formData.append('project_name', projectName);
        formData.append('sem_start', document.querySelector('input[name="sem_start"]')?.value || '2026-01-12');
        formData.append('sem_end', document.querySelector('input[name="sem_end"]')?.value || '2026-05-15');

        const res = await fetch('/process', { method: 'POST', body: formData });
        const data = await res.json();

        if (data.status === 'success') {
        voiceStatus.innerHTML = '✅ Tasks extracted! Refreshing view...';
        setTimeout(() => window.location.reload(), 1500);
        } else {
        voiceStatus.innerHTML = `❌ ${data.error || 'Processing failed'}`;
        }
    } catch (err) {
        console.error(err);
        voiceStatus.innerHTML = `❌ Network error: ${err.message}`;
    }
    }
});