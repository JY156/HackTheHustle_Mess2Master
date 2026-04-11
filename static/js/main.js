document.addEventListener('DOMContentLoaded', async () => {
    // === DOM Elements ===
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');
    const form = document.getElementById('uploadForm');
    const submitBtn = document.getElementById('submitBtn');
    const projectSelect = document.getElementById('projectSelect');
    const newProjectInput = document.getElementById('newProjectInput');
    const projectHint = document.getElementById('projectHint');
    const semesterBanner = document.getElementById('semesterBanner');
    const saveSemesterBtn = document.getElementById('saveSemesterBtn');
    const resetSemesterBtn = document.getElementById('resetSemesterBtn');

    // === 1. Check Semester Status on Load ===
    async function checkSemester() {
        try {
            const res = await fetch('/api/semester/status');
            const data = await res.json();
            // Show banner only if semester is NOT configured
            if (semesterBanner) {
                semesterBanner.style.display = data.configured ? 'none' : 'block';
            }
        } catch (err) {
            console.warn('Semester check failed:', err);
            // Show banner to be safe if API fails
            if (semesterBanner) semesterBanner.style.display = 'block';
        }
    }
    checkSemester();

    // === 2. Save Semester Settings ===
    saveSemesterBtn?.addEventListener('click', async () => {
        const start = document.getElementById('semStart').value;
        const end = document.getElementById('semEnd').value;
        const breakWeek = document.getElementById('breakWeek').value;
        
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
            if (data.status === 'reset') {
                window.location.reload();
            }
        } catch (err) {
            console.error('Reset failed:', err);
            alert('Failed to reset semester');
        }
    });

    // === 5. Drag & Drop UI ===
    dropZone?.addEventListener('click', () => fileInput?.click());
    dropZone?.addEventListener('dragover', (e) => { 
        e.preventDefault(); 
        dropZone.style.borderColor = 'var(--primary)'; 
    });
    dropZone?.addEventListener('dragleave', () => { 
        dropZone.style.borderColor = 'var(--border)'; 
    });
    dropZone?.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--border)';
        if (fileInput) {
            fileInput.files = e.dataTransfer.files;
            updateFileList();
        }
    });
    fileInput?.addEventListener('change', updateFileList);

    function updateFileList() {
        if (!fileList || !fileInput) return;
        const files = Array.from(fileInput.files || []);
        
        if (files.length === 0) {
            fileList.textContent = 'Supports multimodal input';
        } else if (files.length === 1) {
            fileList.textContent = files[0].name;
        } else {
            // Show count + first file + ellipsis for multiple
            fileList.textContent = `${files.length} files: ${files[0].name}${files.length > 2 ? ', ...' : files.length === 2 ? ', ' + files[1].name : ''}`;
        }
        // visual feedback for multiple files
        if (dropZone) {
        dropZone.classList.toggle('multiple-files', files.length > 1);
    }
    }

    // === 6. Form Submit (Smart Project Resolution) ===
    form?.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const newProjectVal = document.getElementById('newProjectInput')?.value.trim();
        const existingProjectVal = document.getElementById('projectSelect')?.value;
        
        // 🔑 Priority: New Input > Dropdown
        let projectName = '';
        if (newProjectVal) {
            projectName = newProjectVal;
        } else if (existingProjectVal) {
            projectName = existingProjectVal;
        }

        if (!projectName) {
            alert('⚠️ Please select an existing project OR type a new project name');
            return;
        }
        
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing AI...';

        const formData = new FormData(form);
        formData.set('project_name', projectName); // ✅ Forces correct value to backend

        try {
            const res = await fetch('/process', { method: 'POST', body: formData });
            const data = await res.json();
            
            if (data.status === 'success') {
                window.location.href = '/tasks';
            } else {
                alert(data.error || 'Processing completed with unexpected format.');
            }
        } catch (err) {
            console.error('Upload failed:', err);
            alert('Error processing upload. Check console.');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Generate Plan';
        }
    });

    // === 7. Calendar Redirect ===
    document.querySelectorAll('.btn-calendar')?.forEach(btn => {
        btn.addEventListener('click', () => {
            const title = encodeURIComponent(btn.dataset.title || 'Mess2Master Task');
            const date = (btn.dataset.date || btn.dataset.deadline || new Date().toISOString().split('T')[0]).replace(/-/g, '');
            const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${date}/${date}&details=Task%20extracted%20by%20Mess2Master%20AI`;
            window.open(url, '_blank');
        });
    });
});