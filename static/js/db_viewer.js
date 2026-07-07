async function fetchTables() {
    const listEl = document.getElementById('tableList');
    listEl.innerHTML = '<li class="table-item"><span class="text-muted">Loading tables...</span></li>';
    
    try {
        const response = await fetch('/api/db/tables');
        if (!response.ok) throw new Error('Failed to fetch tables');
        const data = await response.json();
        
        listEl.innerHTML = '';
        data.tables.forEach(t => {
            const li = document.createElement('li');
            li.className = 'table-item';
            li.innerHTML = `
                <span><i class="fa-solid fa-table" style="color: var(--text-muted); margin-right: 8px;"></i>${t.name}</span>
                <span class="badge">${t.count} rows</span>
            `;
            li.onclick = () => loadTableData(t.name, li);
            listEl.appendChild(li);
        });
    } catch (err) {
        listEl.innerHTML = `<li class="table-item"><span class="text-danger">Error: ${err.message}</span></li>`;
    }
}

async function loadTableData(tableName, listItemEl = null) {
    if (listItemEl) {
        document.querySelectorAll('.table-item').forEach(el => el.classList.remove('active'));
        listItemEl.classList.add('active');
    }
    
    document.getElementById('currentTableName').textContent = tableName;
    document.getElementById('currentTableName').dataset.table = tableName;
    document.getElementById('refreshDataBtn').disabled = false;
    
    const limit = document.getElementById('limitSelect').value;
    const tBody = document.getElementById('tableBody');
    const tHead = document.getElementById('tableHeaders');
    
    tBody.innerHTML = '<tr><td style="text-align: center; padding: 2rem;">Loading...</td></tr>';
    tHead.innerHTML = '';
    
    try {
        const response = await fetch(`/api/db/tables/${tableName}?limit=${limit}`);
        if (!response.ok) throw new Error('Failed to fetch data');
        const data = await response.json();
        
        document.getElementById('rowCount').textContent = `Showing top ${data.rows.length} rows`;
        
        if (data.columns.length === 0) {
            tBody.innerHTML = '<tr><td class="text-muted" style="text-align: center; padding: 2rem;">No columns found</td></tr>';
            return;
        }
        
        // Headers
        data.columns.forEach(col => {
            const th = document.createElement('th');
            th.textContent = col;
            tHead.appendChild(th);
        });
        
        // Rows
        tBody.innerHTML = '';
        if (data.rows.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td colspan="${data.columns.length}" class="text-muted" style="text-align: center; padding: 2rem;">Table is empty</td>`;
            tBody.appendChild(tr);
            return;
        }
        
        data.rows.forEach(row => {
            const tr = document.createElement('tr');
            data.columns.forEach(col => {
                const td = document.createElement('td');
                let val = row[col];
                if (val === null) val = '<span class="text-muted">NULL</span>';
                else if (typeof val === 'object') val = JSON.stringify(val);
                td.innerHTML = val;
                td.title = td.textContent; // tooltip
                tr.appendChild(td);
            });
            tBody.appendChild(tr);
        });
        
    } catch (err) {
        tBody.innerHTML = `<tr><td class="text-danger" style="text-align: center; padding: 2rem;">Error: ${err.message}</td></tr>`;
    }
}

document.getElementById('refreshTablesBtn').onclick = fetchTables;
document.getElementById('refreshDataBtn').onclick = () => {
    const table = document.getElementById('currentTableName').dataset.table;
    if (table) loadTableData(table);
};
document.getElementById('limitSelect').onchange = () => {
    const table = document.getElementById('currentTableName').dataset.table;
    if (table) loadTableData(table);
};

// Init
fetchTables();
