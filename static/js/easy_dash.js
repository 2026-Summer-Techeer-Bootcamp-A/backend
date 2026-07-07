// --- Global State ---
let currentExtractedSkills = [];
let currentPosition = "backend";
let currentCareerMin = 0;
let currentCareerMax = 3;
let currentSessionId = null;
let currentResumeId = null;

// --- Utility Functions ---
function switchView(viewId, element) {
    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.easy-nav-item').forEach(el => el.classList.remove('active'));
    
    document.getElementById(viewId).classList.add('active');
    element.classList.add('active');
    document.getElementById('current-view-title').innerText = element.innerText;
}

function logAction(method, path, body, resStatus, resBody) {
    const consoleEl = document.getElementById('debug-console');
    const time = new Date().toLocaleTimeString();
    let isErr = resStatus >= 400 || resStatus === 0;
    
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    
    let reqStr = '';
    if (body) {
        if (body instanceof FormData) reqStr = 'FormData (File Upload)';
        else reqStr = typeof body === 'object' ? JSON.stringify(body) : body;
    }
    
    let resStr = typeof resBody === 'object' ? JSON.stringify(resBody, null, 2) : resBody;
    
    entry.innerHTML = `
        <div class="log-meta">
            <span class="log-req">[REQ] ${time}</span>
            <span>${method} ${path}</span>
        </div>
        ${reqStr ? `<div style="color:#666; margin-bottom: 4px;">&gt; ${reqStr}</div>` : ''}
        <div class="log-meta" style="margin-top: 4px;">
            <span class="log-${isErr ? 'err' : 'res'}">[RES] ${resStatus}</span>
        </div>
        <div style="color: ${isErr ? '#ef4444' : '#10b981'};">${resStr}</div>
    `;
    consoleEl.appendChild(entry);
    consoleEl.scrollTop = consoleEl.scrollHeight;
}

function clearLogs() {
    document.getElementById('debug-console').innerHTML = '';
}

async function fetchApi(method, path, body = null, isMultipart = false) {
    const token = localStorage.getItem('access_token');
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    
    let options = { method, headers };
    if (body) {
        if (isMultipart) {
            options.body = body;
        } else {
            headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }
    }
    
    try {
        const res = await fetch(path, options);
        const isJson = res.headers.get('content-type')?.includes('application/json');
        const resData = isJson ? await res.json() : await res.text();
        logAction(method, path, body, res.status, resData);
        return { status: res.status, data: resData };
    } catch (e) {
        logAction(method, path, body, 0, e.message);
        throw e;
    }
}

function updateMatchTargetInfo() {
    let text = "선택된 이력서나 세션이 없습니다. 이력서 관리 탭을 먼저 방문하세요.";
    if (currentResumeId) text = `현재 이력서 ID 사용 중: ${currentResumeId}`;
    else if (currentSessionId) text = `임시 세션 ID 사용 중: ${currentSessionId.substring(0,8)}...`;
    document.getElementById('match-target-info').innerText = text;
}

// ================= View 1: Auth & Me =================
async function doLogin() {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-pass').value;
    const res = await fetchApi('POST', '/auth/login', { email, password });
    if (res.status === 200 && res.data.access_token) {
        localStorage.setItem('access_token', res.data.access_token);
        window.dispatchEvent(new Event('storage'));
        doGetMe();
    }
}

async function doSignup() {
    const email = document.getElementById('signup-email').value;
    const password = document.getElementById('signup-pass').value;
    const nickname = document.getElementById('signup-nick').value;
    const res = await fetchApi('POST', '/auth/signup', { email, password, nickname });
    if (res.status === 201) {
        document.getElementById('login-email').value = email;
        document.getElementById('login-pass').value = password;
        alert("회원가입 성공! 로그인 하세요.");
    }
}

async function doLogout() {
    await fetchApi('POST', '/auth/logout');
    localStorage.removeItem('access_token');
    window.dispatchEvent(new Event('storage'));
    document.getElementById('me-result').innerText = "(Not loaded)";
}

async function doGetMe() {
    const res = await fetchApi('GET', '/auth/me');
    document.getElementById('me-result').innerText = JSON.stringify(res.data, null, 2);
}

// ================= View 2: Resume Manager =================
async function doParseResume() {
    const fileInput = document.getElementById('resume-file');
    if (!fileInput.files.length) return alert("PDF 파일을 선택해주세요.");
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    const res = await fetchApi('POST', '/resume/parse', formData, true);
    if (res.status === 200 && res.data.skills) {
        currentExtractedSkills = res.data.skills;
        currentPosition = res.data.position || "backend";
        currentCareerMin = res.data.career_min || 0;
        currentCareerMax = res.data.career_max || 3;
        document.getElementById('parsed-skills-list').innerText = JSON.stringify(res.data.skills.map(s=>s.canonical));
    }
}

async function doConfirmResume() {
    if (currentExtractedSkills.length === 0) return alert("먼저 이력서를 업로드하여 파싱해주세요.");
    const pool = document.getElementById('resume-pool').value;
    const res = await fetchApi('POST', '/resume/confirm', {
        skills: currentExtractedSkills, position: currentPosition,
        career_min: currentCareerMin, career_max: currentCareerMax, pool
    });
    if (res.status === 200 && res.data.session_id) {
        currentSessionId = res.data.session_id;
        currentResumeId = null;
        document.getElementById('session-display').innerText = `Session: ${currentSessionId}`;
        document.getElementById('current-resume-display').innerText = `Current: Session`;
        updateMatchTargetInfo();
    }
}

async function doSaveResume() {
    const title = document.getElementById('resume-title').value || "내 이력서";
    const pool = document.getElementById('resume-pool').value;
    const res = await fetchApi('POST', '/resume', {
        title, skills: currentExtractedSkills, position: currentPosition,
        career_min: currentCareerMin, career_max: currentCareerMax, pool
    });
    if (res.status === 201) {
        doLoadResumes();
    }
}

async function doLoadResumes() {
    const res = await fetchApi('GET', '/resume');
    const listEl = document.getElementById('resume-list');
    listEl.innerHTML = '';
    if (res.status === 200 && res.data.items) {
        res.data.items.forEach(item => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-sm';
            btn.innerText = `ID: ${item.id} (${item.title})`;
            btn.onclick = () => {
                currentResumeId = item.id;
                currentSessionId = null;
                document.getElementById('current-resume-display').innerText = `Current: ID ${item.id}`;
                updateMatchTargetInfo();
            };
            listEl.appendChild(btn);
        });
    }
}

async function doGetResume() {
    if (!currentResumeId) return alert("이력서를 먼저 선택해주세요.");
    await fetchApi('GET', `/resume/${currentResumeId}`);
}
async function doUpdateResume() {
    if (!currentResumeId) return alert("이력서를 먼저 선택해주세요.");
    await fetchApi('PUT', `/resume/${currentResumeId}`, { title: "Updated Title" });
}
async function doDeleteResume() {
    if (!currentResumeId) return alert("이력서를 먼저 선택해주세요.");
    await fetchApi('DELETE', `/resume/${currentResumeId}`);
    currentResumeId = null;
    updateMatchTargetInfo();
    doLoadResumes();
}

// ================= View 3: My Match =================
function getMatchQueryBase() {
    if (currentResumeId) return `resume_id=${currentResumeId}`;
    if (currentSessionId) return `session_id=${currentSessionId}`;
    return null;
}

async function doGetCoverage() {
    const q = getMatchQueryBase();
    if (!q) return alert("이력서나 세션을 먼저 설정해주세요.");
    const res = await fetchApi('GET', `/match/coverage?${q}&pool=global&position=${currentPosition}&top_k=5`);
    if (res.status === 200 && res.data.coverage_score !== undefined) {
        document.getElementById('match-coverage-score').innerText = `${res.data.coverage_score}%`;
        const html = res.data.top_skills.map(s => {
            const color = s.owned ? 'var(--success)' : 'var(--danger)';
            return `<div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span>${s.canonical} ${s.owned?'✅':'❌'}</span>
                <span style="color:${color}">${(s.freq*100).toFixed(1)}%</span>
            </div>
            <div class="chart-bar-container"><div class="chart-bar-fill" style="width:${s.freq*100}%; background:${color}"></div></div>`;
        }).join('');
        document.getElementById('match-coverage-list').innerHTML = html;
    }
}

async function doGetGap() {
    const q = getMatchQueryBase();
    if (!q) return;
    const res = await fetchApi('GET', `/match/gap?${q}&pool=global&position=${currentPosition}`);
    if (res.status === 200) {
        document.getElementById('match-gap-list').innerText = "부족한 기술 Top 5: " + res.data.gap_top5.map(s=>s.canonical).join(', ');
        document.getElementById('match-radar-list').innerText = "카테고리별 갭: " + res.data.radar.map(r=>`${r.category}(${(r.coverage*100).toFixed(0)}%)`).join(', ');
    }
}

async function doGetWhatIf() {
    const q = getMatchQueryBase();
    if (!q) return;
    const add = document.getElementById('whatif-skill').value;
    const res = await fetchApi('GET', `/match/what-if?${q}&pool=global&add=${add}`);
    if (res.status === 200) {
        document.getElementById('match-whatif-res').innerText = `${add} 추가 시: 매칭 공고 +${res.data.delta}건 증가 (${res.data.matched_before} -> ${res.data.matched_after})`;
    }
}

async function doGetCertGap() {
    const q = getMatchQueryBase();
    if (!q) return;
    const res = await fetchApi('GET', `/cert/gap?${q}&pool=global&position=${currentPosition}`);
    if (res.status === 200) {
        document.getElementById('match-certgap-res').innerText = "부족한 자격증: " + (res.data.gap.map(c=>c.name).join(', ') || "없음");
    }
}

// ================= View 4: Market Stats =================
let acTimeout;
async function doGetSkills(q) {
    clearTimeout(acTimeout);
    acTimeout = setTimeout(async () => {
        if (!q) return document.getElementById('ac-skills').innerText = '';
        const res = await fetchApi('GET', `/skills?q=${q}&limit=5`);
        if (res.status === 200) document.getElementById('ac-skills').innerText = res.data.skills.map(s=>s.canonical).join(', ');
    }, 300);
}

async function doGetJobs() { await fetchApi('GET', '/job-categories'); }
async function doGetIndustries() { await fetchApi('GET', '/industries?pool=domestic'); }
async function doGetCerts(q) { await fetchApi('GET', `/certs?q=${q||'a'}`); }

async function doGetSkillShare() {
    const pool = document.getElementById('stat-pool').value;
    const pos = document.getElementById('stat-pos').value;
    const res = await fetchApi('GET', `/stats/skills?pool=${pool}&position=${pos}&limit=5`);
    if (res.status === 200) {
        const html = res.data.skills.map(s => `
            <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
                <span>${s.canonical}</span><span>${(s.share*100).toFixed(1)}%</span>
            </div>
            <div class="chart-bar-container" style="margin-bottom:8px;"><div class="chart-bar-fill" style="width:${s.share*100}%;"></div></div>
        `).join('');
        document.getElementById('stat-skill-share').innerHTML = html;
    }
}

async function doGetCooccur() {
    const skill = document.getElementById('co-skill').value;
    const res = await fetchApi('GET', `/stats/cooccurrence?skill=${skill}&pool=global&limit=5`);
    if (res.status === 200) document.getElementById('stat-cooccur').innerText = res.data.co_occurs.map(s=>`${s.canonical}(${(s.co_rate*100).toFixed(0)}%)`).join(', ');
}

async function doGetIndStats() {
    const inds = document.getElementById('ind-names').value;
    const res = await fetchApi('GET', `/stats/industry?pool=domestic&industries=${inds}&limit=5`);
    if (res.status === 200) document.getElementById('stat-industry').innerText = JSON.stringify(res.data.by_industry);
}

async function doGetTrend() {
    const skill = document.getElementById('trend-skill').value;
    const res = await fetchApi('GET', `/stats/trend?skill=${skill}&pool=global&interval=year`);
    if (res.status === 200) document.getElementById('stat-trend').innerText = JSON.stringify(res.data.series);
}

async function doGetRising() {
    const res = await fetchApi('GET', `/trend/rising?skills=Rust,Bun`);
    if (res.status === 200) document.getElementById('stat-misc').innerText = JSON.stringify(res.data.signals);
}

async function doGetNews() {
    const res = await fetchApi('GET', `/news`);
    if (res.status === 200) document.getElementById('stat-misc').innerHTML = res.data.items.map(n=>`<div><a href="${n.url}" target="_blank" style="color:var(--primary);">${n.title}</a></div>`).join('');
}

async function doGetCompany() {
    const skill = document.getElementById('comp-skill').value;
    const res = await fetchApi('GET', `/company/by-skill?skill=${skill}&pool=domestic`);
    if (res.status === 200) document.getElementById('stat-comp').innerText = "Present: " + res.data.present.map(c=>c.company).join(', ');
}

// ================= View 5: Jobs Search =================
async function doSearchJobs() {
    const pool = document.getElementById('job-pool').value;
    const pos = document.getElementById('job-pos').value;
    const sort = document.getElementById('job-sort').value;
    const match = document.getElementById('job-match').checked;
    
    let url = `/postings?pool=${pool}&position=${pos}&sort=${sort}&page=1`;
    if (match) {
        const q = getMatchQueryBase();
        if (q) url += `&match_only=true&${q}`;
    }
    
    const res = await fetchApi('GET', url);
    if (res.status === 200) {
        document.getElementById('job-list').innerHTML = res.data.items.map(j => 
            `<div style="padding:0.25rem; border-bottom:1px solid var(--panel-border); cursor:pointer;" onclick="document.getElementById('selected-job-id').value='${j.id}'">
                <strong>${j.company}</strong>: ${j.title} (ID: ${j.id})
            </div>`
        ).join('');
    }
}

async function doGetMap() {
    const res = await fetchApi('GET', `/postings/map?region=Seoul`);
    if (res.status === 200) document.getElementById('job-map').innerText = `${res.data.pins.length}개의 핀 데이터를 로드했습니다.`;
}

async function doGetJobDetail() {
    const id = document.getElementById('selected-job-id').value;
    if (!id) return alert("검색 결과에서 공고를 선택해주세요.");
    const res = await fetchApi('GET', `/postings/${id}`);
    if (res.status === 200) document.getElementById('job-action-res').innerText = JSON.stringify(res.data, null, 2);
}

async function doGetSimilar() {
    const id = document.getElementById('selected-job-id').value;
    if (!id) return alert("검색 결과에서 공고를 선택해주세요.");
    const res = await fetchApi('GET', `/postings/${id}/similar?limit=3`);
    if (res.status === 200) document.getElementById('job-action-res').innerText = JSON.stringify(res.data.similar, null, 2);
}

async function doJobAnalysis() {
    const id = document.getElementById('selected-job-id').value;
    if (!id) return alert("검색 결과에서 공고를 선택해주세요.");
    const res = await fetchApi('GET', `/postings/${id}/analysis`);
    if (res.status === 200) document.getElementById('job-action-res').innerText = JSON.stringify(res.data, null, 2);
}

// ================= View 6: AI Assistant =================
function addChatBubble(text, isAi) {
    const box = document.getElementById('chat-box');
    const msg = document.createElement('div');
    msg.className = `chat-msg ${isAi ? 'ai' : 'user'}`;
    msg.innerText = text;
    box.appendChild(msg);
    box.scrollTop = box.scrollHeight;
}

async function doSendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    
    addChatBubble(msg, false);
    input.value = '';
    
    const pool = document.getElementById('chat-pool').value;
    const res = await fetchApi('POST', '/chat', { message: msg, pool: pool });
    
    if (res.status === 200) {
        addChatBubble(res.data.answer, true);
    } else {
        addChatBubble("AI에 연결하는 중 오류가 발생했습니다.", true);
    }
}

async function doResumeFeedback() {
    const q = getMatchQueryBase();
    if (!q) return alert("이력서나 세션을 먼저 설정해주세요.");
    
    let body = { position: currentPosition };
    if (currentResumeId) body.resume_id = currentResumeId;
    if (currentSessionId) body.session_id = currentSessionId;
    
    const res = await fetchApi('POST', '/resume/feedback', body);
    if (res.status === 200) {
        document.getElementById('feedback-res').innerText = JSON.stringify(res.data, null, 2);
    }
}

// ================= View 7: Admin Panel =================
async function doAdminStatus() {
    const res = await fetchApi('GET', '/admin/collector/status');
    if (res.status === 200) document.getElementById('admin-status-res').innerText = JSON.stringify(res.data, null, 2);
}

async function doAdminRun() {
    const src = document.getElementById('admin-source').value;
    const body = src ? { source: src } : {};
    await fetchApi('POST', '/admin/collector/run', body);
}
