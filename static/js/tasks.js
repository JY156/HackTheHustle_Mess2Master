// === View Switching Function ===
function filterTasks(projectSlug) {
    console.log('Filtering view for:', projectSlug);
    
    // 1. Update Active Button
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.remove('active');
        btn.style.background = 'var(--surface)';
        btn.style.color = 'var(--text)';
    });
    
    const activeBtn = document.getElementById(`filter-${projectSlug}`);
    if (activeBtn) {
        activeBtn.classList.add('active');
        activeBtn.style.background = 'var(--primary)';
        activeBtn.style.color = 'white';
    }
    
    // 2. Toggle Views
    const masterSection = document.getElementById('master-queue-section');
    const allCards = document.querySelectorAll('.project-section');
    
    if (projectSlug === 'all') {
        // Show Master List, Hide Cards
        if (masterSection) masterSection.style.display = 'block';
        allCards.forEach(card => card.style.display = 'none');
        document.getElementById('queueTitle').textContent = 'All Tasks';
    } else {
        // Hide Master List, Show Specific Card
        if (masterSection) masterSection.style.display = 'none';
        
        const targetCard = document.getElementById(`card-${projectSlug}`);
        if (targetCard) {
            targetCard.style.display = 'block';
        }
        
        // Hide other cards
        allCards.forEach(card => {
            if (card.id !== `card-${projectSlug}`) card.style.display = 'none';
        });
        
        // Update Title
        const projectName = projectSlug.replace(/-/g, ' ');
        document.getElementById('queueTitle').textContent = `${projectName} Tasks`;
    }
    
    // Update URL Hash
    window.location.hash = projectSlug;
}

// === Main DOMContentLoaded Handler ===
document.addEventListener('DOMContentLoaded', () => {
    console.log('Tasks.js loaded');

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

    document.querySelectorAll('.alert-jump').forEach(marker => {
        marker.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const target = marker.dataset.alertTarget;
            if (!target) return;
            window.location.hash = target.replace('#', '');
            const targetEl = document.querySelector(target);
            if (targetEl) {
                targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        });
    });

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
    
    // === Filter Button Click Handlers ===
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const projectSlug = e.currentTarget.dataset.filter;
            filterTasks(projectSlug);
        });
    });
    
    // === Task Completion Toggle ===
    document.querySelectorAll('.complete-toggle').forEach(checkbox => {
        checkbox.addEventListener('change', async (e) => {
            const taskEl = e.target.closest('.task-item');
            const taskId = taskEl?.dataset.id;
            const project = taskEl?.dataset.project;
            
            if (!taskId || !project) return;
            
            try {
                const res = await fetch('/api/tasks/complete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({task_id: taskId, project_name: project})
                });
                const data = await res.json();
                
                if (data.status === 'completed') {
                    taskEl.classList.add('completed');
                    const title = taskEl.querySelector('.task-title');
                    if (title && !title.innerHTML.includes('<s>')) {
                        title.innerHTML = `<s>${title.textContent}</s>`;
                    }
                } else if (data.status === 'pending') {
                    taskEl.classList.remove('completed');
                    const title = taskEl.querySelector('.task-title');
                    if (title) {
                        title.innerHTML = title.textContent.replace('<s>', '').replace('</s>', '');
                    }
                }
            } catch (err) {
                console.error('Toggle failed:', err);
                e.target.checked = !e.target.checked;
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

    // === Calendar Redirect (per-task buttons) ===
    document.querySelectorAll('.btn-calendar-icon').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const title = encodeURIComponent(btn.dataset.title || 'Mess2Master Task');
            const date = (btn.dataset.date || new Date().toISOString().split('T')[0]).replace(/-/g, '');
            const formatted = date.replace(/-/g, '');
            const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${formatted}/${formatted}&details=Task%20extracted%20by%20Mess2Master%20AI`;
            window.open(url, '_blank');
        });
    });
    
    // === Sync Button Handler (Per Project) ===
    document.querySelectorAll('.card-sync-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const projectName = e.currentTarget.dataset.project;
            const statusEl = e.currentTarget.nextElementSibling; // The <p> tag below button
            
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Syncing...';
            statusEl.textContent = 'Syncing to Notion...';
            statusEl.className = 'sync-status loading';

            try {
                const res = await fetch('/sync-notion', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ project_name: projectName })
                });
                const data = await res.json();

                if (res.ok && data.status === 'success') {
                    setNotionSuccessStatus(statusEl, data.synced_count, data.dashboard_url);
                } else {
                    // Fallback
                    if (data.clipboard_markdown) {
                        await navigator.clipboard.writeText(data.clipboard_markdown);
                        statusEl.textContent = '✅ Checklist copied to clipboard!';
                        statusEl.className = 'sync-status success';
                        alert('📋 Markdown checklist copied! Open Notion → New Page → Ctrl+V');
                    } else {
                        statusEl.textContent = `❌ Sync failed: ${data.message || 'Unknown error'}`;
                        statusEl.className = 'sync-status error';
                    }
                }
            } catch (err) {
                console.error(err);
                statusEl.textContent = `❌ Error: ${err.message}`;
                statusEl.className = 'sync-status error';
            } finally {
                btn.disabled = false;
                btn.innerHTML = `<i class="fas fa-database"></i> Sync ${projectName} to Notion`;
            }
        });
    });

    // === Reset Semester Button ===
    document.getElementById('resetSemesterBtn')?.addEventListener('click', async () => {
        if (!confirm('⚠️ This will delete all projects and tasks. Start fresh?')) return;
        try {
            const res = await fetch('/api/reset', {method: 'POST'});
            const data = await res.json();
            if (data.status === 'reset') window.location.href = '/';
        } catch (err) {
            console.error('Reset failed:', err);
            alert('Failed to reset semester');
        }
    });

    // === Apply Filter from URL Hash on Load ===
    const hash = window.location.hash.replace('#', '');
    if (hash) {
        setTimeout(() => filterTasks(hash), 100);
    }
});