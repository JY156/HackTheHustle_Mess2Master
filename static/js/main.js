document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');
    const form = document.getElementById('uploadForm');
    const submitBtn = document.getElementById('submitBtn');

    // Drag & Drop UI
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = 'var(--primary)'; });
    dropZone.addEventListener('dragleave', () => { dropZone.style.borderColor = 'var(--border)'; });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--border)';
        fileInput.files = e.dataTransfer.files;
        updateFileList();
    });
    fileInput.addEventListener('change', updateFileList);

    function updateFileList() {
        const files = Array.from(fileInput.files);
        fileList.textContent = files.length ? files.map(f => f.name).join(', ') : 'Supports multimodal input';
    }

    // Form Submit
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing AI...';

        const formData = new FormData(form);
        try {
            const res = await fetch('/process', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.status === 'success') window.location.reload();
            else alert('Processing completed with unexpected format.');
        } catch (err) {
            console.error(err);
            alert('Error processing upload. Check console.');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Generate Plan';
        }
    });

    // Calendar Redirect (Google Calendar URL Template)
    document.querySelectorAll('.btn-calendar').forEach(btn => {
        btn.addEventListener('click', () => {
            const title = encodeURIComponent(btn.dataset.title || 'Mess2Master Task');
            const date = btn.dataset.date || new Date().toISOString().split('T')[0].replace(/-/g, '');
            const formatted = date.replace(/-/g, '');
            const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${formatted}/${formatted}&details=Task%20extracted%20by%20Mess2Master%20AI`;
            window.open(url, '_blank');
        });
    });
});