document.addEventListener('DOMContentLoaded', () => {
    // Project tab switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.project-section').forEach(s => s.style.display = 'none');
            
            btn.classList.add('active');
            const project = btn.dataset.project;
            document.querySelector(`.project-section[data-project="${project}"]`).style.display = 'block';
        });
    });

    // Task completion toggle
    document.querySelectorAll('.complete-toggle').forEach(checkbox => {
        checkbox.addEventListener('change', async (e) => {
            const taskEl = e.target.closest('.task-item');
            const taskId = taskEl.dataset.id;
            const project = taskEl.dataset.project;
            
            try {
                const res = await fetch('/api/tasks/complete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({task_id: taskId, project_name: project})
                });
                const data = await res.json();
                
                if (data.status === 'completed') {
                    taskEl.classList.add('completed');
                    taskEl.querySelector('.task-title').innerHTML = `<s>${taskEl.querySelector('.task-title').textContent}</s>`;
                } else {
                    taskEl.classList.remove('completed');
                    taskEl.querySelector('.task-title').innerHTML = taskEl.querySelector('.task-title').textContent.replace('<s>', '').replace('</s>', '');
                }
            } catch (err) {
                console.error('Toggle failed:', err);
                e.target.checked = !e.target.checked; // Revert on error
            }
        });
    });
});