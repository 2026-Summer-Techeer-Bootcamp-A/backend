const apis = [
    // A. Basic
    { group: 'A. Basic & Auth', method: 'POST', path: '/auth/signup', body: { email: "test@example.com", password: "password", nickname: "tester" } },
    { group: 'A. Basic & Auth', method: 'POST', path: '/auth/login', body: { email: "test@example.com", password: "password" } },
    { group: 'A. Basic & Auth', method: 'POST', path: '/auth/logout' },
    { group: 'A. Basic & Auth', method: 'GET', path: '/auth/me' },
    { group: 'A. Basic & Auth', method: 'POST', path: '/resume/parse', isMultipart: true },
    { group: 'A. Basic & Auth', method: 'POST', path: '/resume/confirm', body: { skills: [{canonical: "Python", category: "language"}], pool: "global" } },
    { group: 'A. Basic & Auth', method: 'POST', path: '/resume', body: { title: "My Resume", skills: [], position: "backend", career_min: 3, career_max: 5, pool: "global" } },
    { group: 'A. Basic & Auth', method: 'GET', path: '/resume' },
    { group: 'A. Basic & Auth', method: 'GET', path: '/resume/{id}', pathParams: ['id'] },
    { group: 'A. Basic & Auth', method: 'PUT', path: '/resume/{id}', pathParams: ['id'], body: { title: "Updated" } },
    { group: 'A. Basic & Auth', method: 'DELETE', path: '/resume/{id}', pathParams: ['id'] },
    { group: 'A. Basic & Auth', method: 'GET', path: '/healthz' },
    
    // B. Analysis
    { group: 'B. Analysis', method: 'GET', path: '/match/coverage', queryParams: ['resume_id', 'pool', 'position', 'top_k'] },
    { group: 'B. Analysis', method: 'GET', path: '/match/gap', queryParams: ['session_id', 'pool', 'position'] },
    { group: 'B. Analysis', method: 'GET', path: '/match/what-if', queryParams: ['resume_id', 'pool', 'add'] },
    { group: 'B. Analysis', method: 'GET', path: '/stats/skills', queryParams: ['pool', 'position', 'limit'] },
    { group: 'B. Analysis', method: 'GET', path: '/stats/cooccurrence', queryParams: ['skill', 'pool', 'limit'] },
    { group: 'B. Analysis', method: 'GET', path: '/stats/industry', queryParams: ['pool', 'industries', 'limit'] },
    { group: 'B. Analysis', method: 'GET', path: '/stats/trend', queryParams: ['skill', 'pool', 'interval'] },
    { group: 'B. Analysis', method: 'GET', path: '/cert/gap', queryParams: ['resume_id', 'pool', 'position'] },
    { group: 'B. Analysis', method: 'GET', path: '/company/by-skill', queryParams: ['skill', 'pool'] },
    { group: 'B. Analysis', method: 'GET', path: '/postings', queryParams: ['pool', 'position', 'sort', 'match_only', 'resume_id', 'page'] },
    { group: 'B. Analysis', method: 'GET', path: '/postings/map', queryParams: ['region', 'bbox'] },
    { group: 'B. Analysis', method: 'GET', path: '/trend/rising', queryParams: ['skills'] },
    { group: 'B. Analysis', method: 'GET', path: '/postings/{id}', pathParams: ['id'] },
    { group: 'B. Analysis', method: 'GET', path: '/postings/{id}/similar', pathParams: ['id'], queryParams: ['limit'] },
    { group: 'B. Analysis', method: 'GET', path: '/postings/{id}/analysis', pathParams: ['id'] },
    { group: 'B. Analysis', method: 'GET', path: '/skills', queryParams: ['q', 'category', 'limit'] },
    { group: 'B. Analysis', method: 'GET', path: '/job-categories' },
    { group: 'B. Analysis', method: 'GET', path: '/industries', queryParams: ['pool'] },
    { group: 'B. Analysis', method: 'GET', path: '/certs', queryParams: ['q'] },
    { group: 'B. Analysis', method: 'POST', path: '/resume/feedback', body: { session_id: "xyz", position: "backend" } },
    { group: 'B. Analysis', method: 'GET', path: '/news' },
    { group: 'B. Analysis', method: 'POST', path: '/chat', body: { message: "Hello", pool: "global" } },
    
    // C. Admin
    { group: 'C. Admin', method: 'GET', path: '/admin/collector/status' },
    { group: 'C. Admin', method: 'POST', path: '/admin/collector/run', body: { source: "himalayas" } }
];

let selectedApi = null;
let lastResponse = null;

function renderApiList(search = '') {
    const container = document.getElementById('apiList');
    container.innerHTML = '';
    
    // Group APIs
    const grouped = apis.reduce((acc, api) => {
        if (!acc[api.group]) acc[api.group] = [];
        if (api.path.toLowerCase().includes(search.toLowerCase())) {
            acc[api.group].push(api);
        }
        return acc;
    }, {});
    
    for (const [group, groupApis] of Object.entries(grouped)) {
        if (groupApis.length === 0) continue;
        
        const title = document.createElement('div');
        title.className = 'api-group-title';
        title.textContent = group;
        container.appendChild(title);
        
        groupApis.forEach((api, index) => {
            const item = document.createElement('div');
            item.className = 'api-item';
            item.innerHTML = `
                <span class="method-badge method-${api.method.toLowerCase()}">${api.method}</span>
                <span class="api-path">${api.path}</span>
            `;
            item.onclick = () => selectApi(api, item);
            container.appendChild(item);
        });
    }
}

function selectApi(api, element) {
    document.querySelectorAll('.api-item').forEach(el => el.classList.remove('active'));
    element.classList.add('active');
    selectedApi = api;
    
    document.getElementById('selectedApiBadge').textContent = `${api.method} ${api.path}`;
    document.getElementById('sendRequestBtn').disabled = false;
    
    // Toggle input containers
    const pathContainer = document.getElementById('requestPathContainer');
    const queryContainer = document.getElementById('requestQueryContainer');
    const bodyContainer = document.getElementById('requestBodyContainer');
    const fileContainer = document.getElementById('requestFileContainer');
    
    pathContainer.style.display = api.pathParams ? 'block' : 'none';
    queryContainer.style.display = api.queryParams ? 'block' : 'none';
    bodyContainer.style.display = (api.body && !api.isMultipart) ? 'block' : 'none';
    fileContainer.style.display = api.isMultipart ? 'block' : 'none';
    
    // Build Path Params
    if (api.pathParams) {
        document.getElementById('pathParams').innerHTML = api.pathParams.map(p => `
            <div class="param-row">
                <span class="param-label">{${p}}</span>
                <input type="text" id="path_${p}" class="input-control" placeholder="value">
            </div>
        `).join('');
    }
    
    // Build Query Params
    if (api.queryParams) {
        document.getElementById('queryParams').innerHTML = api.queryParams.map(p => `
            <div class="param-row">
                <span class="param-label">${p}=</span>
                <input type="text" id="query_${p}" class="input-control" placeholder="value (optional)">
            </div>
        `).join('');
    }
    
    // Build Body
    if (api.body) {
        document.getElementById('requestBody').value = JSON.stringify(api.body, null, 2);
    } else {
        document.getElementById('requestBody').value = '';
    }
}

document.getElementById('requestForm').onsubmit = async (e) => {
    e.preventDefault();
    if (!selectedApi) return;
    
    const sendBtn = document.getElementById('sendRequestBtn');
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending...';
    
    document.getElementById('responseStatus').textContent = 'Loading...';
    document.getElementById('responseStatus').className = 'status-indicator';
    document.getElementById('responseBody').textContent = 'Wait...';
    
    try {
        let finalPath = selectedApi.path;
        if (selectedApi.pathParams) {
            selectedApi.pathParams.forEach(p => {
                const val = document.getElementById(`path_${p}`).value || p;
                finalPath = finalPath.replace(`{${p}}`, val);
            });
        }
        
        let url = finalPath;
        if (selectedApi.queryParams) {
            const params = new URLSearchParams();
            selectedApi.queryParams.forEach(p => {
                const val = document.getElementById(`query_${p}`).value;
                if (val) params.append(p, val);
            });
            const qs = params.toString();
            if (qs) url += `?${qs}`;
        }
        
        const headers = {};
        const token = localStorage.getItem('access_token');
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }
        
        let bodyOptions = {};
        if (selectedApi.isMultipart) {
            const fileInput = document.getElementById('requestFile');
            if (fileInput.files.length > 0) {
                const formData = new FormData();
                formData.append('file', fileInput.files[0]);
                bodyOptions = { body: formData };
            }
        } else if (selectedApi.method !== 'GET' && selectedApi.method !== 'DELETE') {
            headers['Content-Type'] = 'application/json';
            bodyOptions = { body: document.getElementById('requestBody').value };
        }
        
        const response = await fetch(url, {
            method: selectedApi.method,
            headers,
            ...bodyOptions
        });
        
        const isJson = response.headers.get('content-type')?.includes('application/json');
        const resData = isJson ? await response.json() : await response.text();
        lastResponse = { status: response.status, data: resData };
        
        // UI Update
        const statusEl = document.getElementById('responseStatus');
        statusEl.textContent = `${response.status} ${response.statusText}`;
        statusEl.className = 'status-indicator';
        if (response.status >= 200 && response.status < 300) statusEl.classList.add('status-2xx');
        else if (response.status >= 400 && response.status < 500) statusEl.classList.add('status-4xx');
        else if (response.status >= 500) statusEl.classList.add('status-5xx');
        
        document.getElementById('responseBody').textContent = isJson ? JSON.stringify(resData, null, 2) : resData;
        
        // Enable Set Token button if it's login
        if (selectedApi.path === '/auth/login' && response.status === 200) {
            document.getElementById('setTokenBtn').disabled = false;
        } else {
            document.getElementById('setTokenBtn').disabled = true;
        }
        
    } catch (err) {
        document.getElementById('responseStatus').textContent = 'Error';
        document.getElementById('responseStatus').className = 'status-indicator status-5xx';
        document.getElementById('responseBody').textContent = String(err);
    } finally {
        sendBtn.disabled = false;
        sendBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Send Request';
    }
};

document.getElementById('apiSearch').addEventListener('input', (e) => {
    renderApiList(e.target.value);
});

document.getElementById('setTokenBtn').addEventListener('click', () => {
    if (lastResponse && lastResponse.data && lastResponse.data.access_token) {
        localStorage.setItem('access_token', lastResponse.data.access_token);
        alert('Token saved to localStorage');
        window.dispatchEvent(new Event('storage'));
    }
});

// Init
renderApiList();
