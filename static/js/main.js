// static/js/main.js - Fetch + display results
document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const formData = new FormData(e.target);
    const response = await fetch('/process', {
        method: 'POST',
        body: formData
    });
    
    const data = await response.json();
    
    // Display tasks
    const tasksDiv = document.getElementById('tasks');
    tasksDiv.innerHTML = data.tasks.map(task => `
        <div class="task priority-${task.priority}">
        <strong>${task.title}</strong>
        <p>${task.description}</p>
        <small>Due: ${task.due_date || 'TBD'} | ${task.priority.toUpperCase()}</small>
        </div>
    `).join('');
    
    document.getElementById('results').style.display = 'block';
    
    // Store data for calendar button
    window.projectData = data;
});

// Add to main.js - Calendar button handler
document.getElementById('addToCalendar').addEventListener('click', async () => {
    const topTask = window.projectData.tasks[0]; // Highest priority
    
    const response = await fetch('/process', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
        files: [], // Re-send if needed, or cache
        notes: "", 
        create_calendar: true,
        top_deadline: {
            title: topTask.title,
            date: topTask.due_date
        }
        })
    });
    
    const data = await response.json();
    alert(data.calendar_result?.message || "Calendar updated!");
});