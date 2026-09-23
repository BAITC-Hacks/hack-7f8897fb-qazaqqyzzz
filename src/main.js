const app = document.getElementById('app');

const state = {
  user: null, people: [], skills: [], roles: [], roleProfiles: [], departments: [], selected: null,
  detail: null, hr: null, plan: null, page: 'today', query: '', catalogFilter: 'recommended',
  aiBusy: false, version: 0, photoDraft: ''
};

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const human = value => String(value || '').replaceAll('_',' ').replace(/\b\w/g, c => c.toUpperCase());
const initials = name => String(name || '?').split(' ').filter(Boolean).slice(0,2).map(x => x[0]).join('').toUpperCase();
const dateLabel = value => value ? new Date(value + 'T12:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : 'Flexible';
const nav = [
  ['today','sparkles','Today'], ['plan','scope','My Plan'], ['opportunities','calendar','Opportunities'],
  ['activity','check','Activity'], ['profile','person','Profile']
];
const icons = {sparkles:'✦',scope:'◎',calendar:'▦',check:'✓',person:'●',team:'◉'};

async function api(path, options={}) {
  const response = await fetch(path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...(state.user?.csrf?{'X-CSRF-Token':state.user.csrf}:{}),...options.headers}});
  let body; try { body = await response.json(); } catch { throw new Error('The server returned an unreadable response.'); }
  if(!response.ok) {
    if(response.status===401 && state.user){ state.user=null; showLogin(); }
    const message = typeof body.detail === 'string' ? body.detail : body.detail?.map?.(x=>x.msg).join('; ');
    throw new Error(message || 'Something went wrong.');
  }
  return body;
}

function toast(message, tone='good') {
  document.querySelector('.toast')?.remove();
  const node=document.createElement('div'); node.className=`toast ${tone}`; node.role='status'; node.textContent=message;
  document.body.append(node); setTimeout(()=>node.remove(),4500);
}

function avatar(employee, extra='') {
  return employee.photo ? `<img class="avatar ${extra}" src="${esc(employee.photo)}" alt="${esc(employee.full_name)}">` : `<span class="avatar ${extra}" aria-hidden="true">${initials(employee.full_name)}</span>`;
}

async function showLogin(mode='login', error='') {
  let catalog={role_profiles:[],departments:[]};
  try { catalog=await api('/api/catalog'); } catch {}
  const roles=[...new Set(catalog.role_profiles.map(x=>x.role))];
  const register = mode==='register';
  app.innerHTML=`<main class="auth-shell">
    <section class="auth-story">
      <div class="wordmark"><span>✦</span> Career Quest</div>
      <div class="auth-copy"><span class="eyebrow">YOUR CAREER, MADE CLEAR</span><h1>Build a future<br>that feels like yours.</h1><p>Turn your goals, skills and real opportunities into a plan you can act on every week.</p></div>
      <div class="quote-card"><span>“</span><p>Great careers are built from small, intentional moves.</p></div>
    </section>
    <section class="auth-panel"><form id="authForm" class="auth-card">
      <div class="mobile-brand">✦ Career Quest</div>
      <h2>${register?'Create your path':'Welcome back'}</h2>
      <p>${register?'Tell us where you are. We will help with what comes next.':'Sign in to continue your personal career journey.'}</p>
      ${register?`<div class="form-grid two"><label>Full name<input name="full_name" required maxlength="120" placeholder="Your name"></label><label>Work email<input name="email" type="email" required placeholder="you@company.com"></label></div>
        <label>Department<input name="department" required list="departments" placeholder="Product, Engineering…"><datalist id="departments">${catalog.departments.map(x=>`<option value="${esc(x)}">`).join('')}</datalist></label>
        <div class="form-grid two"><label>Current role<select name="role" required>${roles.map(x=>`<option>${esc(x)}</option>`).join('')}</select></label><label>Current level<select name="grade">${['Junior','Middle','Senior','Lead'].map(x=>`<option>${x}</option>`).join('')}</select></label></div>`:''}
      <label>Username<input name="username" autocomplete="username" required minlength="3" placeholder="Your username"></label>
      <label>Password<input name="password" type="password" autocomplete="${register?'new-password':'current-password'}" required minlength="${register?10:1}" placeholder="${register?'10+ characters':'Your password'}"></label>
      <p class="form-error" role="alert">${esc(error)}</p>
      <button class="button primary wide" type="submit">${register?'Create my account':'Continue'} <span>→</span></button>
      <button class="button ghost wide" id="switchMode" type="button">${register?'Already have an account? Sign in':'New here? Create an account'}</button>
      <div id="demoArea"></div>
    </form></section>
  </main>`;
  document.getElementById('switchMode').onclick=()=>showLogin(register?'login':'register');
  document.getElementById('authForm').onsubmit=async event=>{
    event.preventDefault(); const form=event.currentTarget, button=form.querySelector('[type=submit]'); button.disabled=true;
    try {
      const values=Object.fromEntries(new FormData(form));
      state.user=await api(register?'/api/auth/register':'/api/auth/login',{method:'POST',body:JSON.stringify(values)});
      await start();
    } catch(e) { showLogin(mode,e.message); }
  };
  if(!register) api('/api/config').then(config=>{
    if(!config.local_demo) return;
    document.getElementById('demoArea').innerHTML='<div class="demo-line"><span>or explore locally</span></div><div class="demo-buttons"><button data-demo="employee" type="button">Employee demo</button><button data-demo="hr" type="button">HR demo</button></div>';
    document.querySelectorAll('[data-demo]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{state.user=await api('/api/auth/demo',{method:'POST',body:JSON.stringify({role:button.dataset.demo})});await start()}catch(e){showLogin('login',e.message)}});
  }).catch(()=>{});
}

async function start() {
  state.user=await api('/api/auth/me');
  await refreshBootstrap();
  state.selected=state.people.some(x=>x.employee_id===state.user.employee_id)?state.user.employee_id:(state.selected||state.people[0]?.employee_id);
  state.page='today'; state.hr=null; await loadProfile();
}

async function refreshBootstrap() {
  const data=await api('/api/bootstrap');
  state.people=data.employees; state.skills=data.skills; state.roles=data.roles; state.roleProfiles=data.role_profiles;
  state.departments=data.departments; state.asOf=data.as_of_date;
}

async function loadProfile(keepPage=true) {
  const version=++state.version, page=state.page; state.plan=null; state.aiBusy=false;
  app.innerHTML='<div class="boot"><div class="boot-mark">✦</div><strong>Career Quest</strong><span>Building your path…</span></div>';
  try {
    const detail=await api('/api/employees/'+encodeURIComponent(state.selected));
    if(version!==state.version) return; state.detail=detail; state.photoDraft=detail.employee.photo||''; state.page=keepPage?page:'today'; render();
  } catch(e) { app.innerHTML=`<div class="boot"><strong>We could not load your path.</strong><span>${esc(e.message)}</span><button id="retry" class="button primary">Try again</button></div>`;document.getElementById('retry').onclick=loadProfile; }
}

async function navigate(page) {
  state.page=page; state.query='';
  if(page==='team'&&!state.hr){ render(); try{state.hr=await api('/api/hr/summary');render()}catch(e){toast(e.message,'bad');state.page='today';render()} }
  else render();
}

function render() {
  if(!state.user||!state.detail) return;
  const e=state.detail.employee;
  const pages=[...nav,...(state.user.role==='hr'?[['team','team','People']]:[])];
  const view={today,plan,opportunities,activity,profile,team}[state.page]?.()||today();
  app.innerHTML=`<div class="app-shell">
    <aside class="sidebar">
      <div class="wordmark"><span>✦</span><b>Career Quest</b></div>
      <nav>${pages.map(([id,icon,label])=>`<button data-page="${id}" class="${state.page===id?'active':''}" ${state.page===id?'aria-current="page"':''}><i>${icons[icon]}</i><span>${label}</span></button>`).join('')}</nav>
      <div class="side-progress"><div><span>Path level ${state.detail.path_level.level}</span><b>${esc(state.detail.path_level.name)}</b></div><div class="mini-track"><i style="width:${state.detail.path_level.progress}%"></i></div><small>${state.detail.path_level.progress}% to next level</small></div>
      <button class="account-chip" data-page="profile">${avatar(e)}<span><b>${esc(e.full_name)}</b><small>${esc(e.grade)} ${esc(e.role)}</small></span><i>›</i></button>
    </aside>
    <main class="content">
      <header class="topbar"><div><span class="mobile-wordmark">✦</span><b>${pageTitle()}</b></div><div class="top-actions">${state.user.role==='hr'?`<select id="employeeSelect" aria-label="View employee">${state.people.map(p=>`<option value="${p.employee_id}" ${p.employee_id===state.selected?'selected':''}>${esc(p.full_name)}</option>`).join('')}</select>`:''}<button id="logout" class="icon-button" title="Sign out">↗</button></div></header>
      <div class="view">${view}</div>
    </main>
    <div id="modal" class="modal-layer" hidden><section class="modal-card"><button id="closeModal" class="modal-close" aria-label="Close">×</button><div id="modalContent"></div></section></div>
  </div>`;
  wire();
}

function pageTitle(){return {today:'Today',plan:'My Career Plan',opportunities:'Opportunities',activity:'Activity',profile:'My Profile',team:'People & Growth'}[state.page]}
function targetLabel(){const t=state.detail.target;return t?`${t.grade} ${t.role}`:'your next role'}
function completionStats(){const completed=state.detail.history.filter(x=>x.status==='completed');const map=Object.fromEntries(state.detail.catalog.map(x=>[x.event_id,x]));return {completed:completed.length,hours:completed.reduce((a,x)=>a+(map[x.event_id]?.duration_hours||0),0)}}

function today() {
  const d=state.detail,e=d.employee,goal=e.career_goal||{},stats=completionStats(),recs=state.plan?.recommendations||d.recommendations;
  const critical=d.skill_progress.filter(x=>x.critical),criticalDone=critical.filter(x=>!x.gap).length;
  return `<section class="welcome-row"><div><p class="kicker">GOOD ${new Date().getHours()<12?'MORNING':new Date().getHours()<18?'AFTERNOON':'EVENING'}</p><h1>Keep moving, ${esc(e.full_name.split(' ')[0])}.</h1><p>Your next meaningful step is already in sight.</p></div><button class="button soft" data-page="profile">Edit profile</button></section>
  <section class="hero-card">
    <div class="hero-glow one"></div><div class="hero-glow two"></div>
    <div class="hero-profile">${avatar(e,'large')}<div><span>YOUR CURRENT DIRECTION</span><h2>${esc(targetLabel())}</h2><p>${esc(goal.statement||'Define what you want to become and Career Quest will shape the path with you.')}</p><button class="hero-link" data-page="plan">Shape my plan →</button></div></div>
    <div class="level-ring" style="--progress:${d.readiness*3.6}deg"><div><b>${d.readiness}%</b><span>path fit</span></div></div>
  </section>
  <section class="metric-grid">
    ${metric('Path level',d.path_level.level,esc(d.path_level.name),'✦')}
    ${metric('Critical skills',`${criticalDone}/${critical.length}`,'ready for your goal','◎')}
    ${metric('Completed',stats.completed,`${stats.hours} focused hours`,'✓')}
    ${metric('Weekly focus',goal.weekly_hours||3,'hours you committed','◷')}
  </section>
  <section class="dashboard-grid">
    <div class="surface wide"><div class="section-head"><div><span class="section-kicker">YOUR NEXT MOVES</span><h2>A plan that moves with you</h2></div><button id="generateAI" class="button primary" ${state.aiBusy?'disabled':''}>${state.aiBusy?'Thinking…':'✦ Refine with AI'}</button></div>
      ${state.plan?.message?`<div class="status-note">${esc(state.plan.message)}${state.plan.cached?' · reused securely':''}</div>`:''}
      <div class="next-list">${recs.slice(0,3).map((x,i)=>nextMove(x,i)).join('')||`<div class="empty-state"><b>Your path needs a new goal.</b><span>${esc(d.no_recommendation_reason)}</span></div>`}</div>
    </div>
    <aside class="surface focus-card"><span class="section-kicker">THIS WEEK</span><h2>One small promise</h2><div class="focus-orb">${Math.min(100,d.path_level.progress)}<small>%</small></div><p>${recs[0]?`Spend 30 minutes starting <b>${esc(recs[0].event.title)}</b>.`:'Write down the role you want and why it matters.'}</p><button class="button soft wide" data-page="plan">Open my plan</button></aside>
  </section>`;
}

function metric(label,value,note,icon){return `<article class="metric"><i>${icon}</i><div><span>${label}</span><b>${value}</b><small>${note}</small></div></article>`}
function nextMove(item,index){const e=item.event;return `<article class="next-move"><span class="move-number">0${index+1}</span><div class="move-main"><span>${esc(human(e.type))} · ${e.duration_hours}h</span><h3>${esc(e.title)}</h3><p>${esc(item.coach_note||item.factors?.[1]||e.description)}</p><div class="skill-pills">${item.effects.slice(0,3).map(x=>`<i>${esc(x.name)} +${x.gain}</i>`).join('')}</div></div><button class="circle-button" data-event="${e.event_id}" aria-label="View ${esc(e.title)}">→</button></article>`}

function goalForm() {
  const goal=state.detail.employee.career_goal||{};
  return `<form id="goalForm" class="form-card"><div class="section-head"><div><span class="section-kicker">DIRECTION</span><h2>Describe where you want to go</h2></div><span class="privacy-chip">Private to you + HR</span></div>
    <label>In your own words<textarea name="statement" required minlength="15" maxlength="1200" placeholder="Example: I want to lead backend projects, become confident in system design and mentor junior engineers.">${esc(goal.statement||'')}</textarea></label>
    <div class="form-grid three"><label>Target role<select name="target_role">${state.roles.map(x=>`<option ${x===(goal.target_role||state.detail.employee.role)?'selected':''}>${esc(x)}</option>`).join('')}</select></label><label>Target level<select name="target_grade">${['Junior','Middle','Senior','Lead'].map(x=>`<option ${x===(goal.target_grade||state.detail.target?.grade)?'selected':''}>${x}</option>`).join('')}</select></label><label>Timeline<input name="timeline" maxlength="80" value="${esc(goal.timeline||'6 months')}" placeholder="6 months"></label></div>
    <label>Hours I can invest each week <input class="range" name="weekly_hours" type="range" min="1" max="30" value="${goal.weekly_hours||3}"><span id="hoursValue" class="range-value">${goal.weekly_hours||3} hours</span></label>
    <button class="button primary" type="submit">Save direction</button>
  </form>`;
}

function plan() {
  const d=state.detail,recs=state.plan?.recommendations||d.recommendations;
  return `<section class="page-intro"><p class="kicker">YOUR NORTH STAR</p><h1>Turn ambition into a weekly plan.</h1><p>Tell us what matters. Your skill evidence and AI will do the organizing.</p></section>
    <div class="plan-layout"><div>${goalForm()}</div><aside class="level-card"><span>PATH LEVEL</span><b>${d.path_level.level}</b><h3>${esc(d.path_level.name)}</h3><div class="big-track"><i style="width:${d.path_level.progress}%"></i></div><p>${d.path_level.next_xp?`${d.path_level.next_xp-d.path_level.xp} XP until your next level`:'Highest level reached'}</p><small>Level grows through relevant skills, completed activities and consistent progress.</small></aside></div>
    <section class="surface plan-output"><div class="section-head"><div><span class="section-kicker">PERSONAL ROADMAP</span><h2>${esc(targetLabel())}</h2></div><button id="generateAI" class="button primary" ${state.aiBusy?'disabled':''}>${state.aiBusy?'Creating…':'✦ Create AI plan'}</button></div>
      ${state.plan?.message?`<div class="status-note">${esc(state.plan.message)}</div>`:''}<div class="roadmap">${recs.map((x,i)=>roadmapItem(x,i)).join('')||'<div class="empty-state">Save a goal to build your roadmap.</div>'}</div></section>
    <section class="surface skill-surface"><div class="section-head"><div><span class="section-kicker">SKILL SIGNALS</span><h2>What your goal asks for</h2></div><b>${d.readiness}% ready</b></div><div class="skill-grid">${d.skill_progress.map(skillBar).join('')}</div></section>`;
}

function roadmapItem(item,index){return `<article class="roadmap-item"><div class="roadmap-dot">${index+1}</div><div><span>STEP ${index+1} · ${esc(human(item.event.format))}</span><h3>${esc(item.event.title)}</h3><p>${esc(item.coach_note||item.explanation)}</p></div><div class="roadmap-side"><b>${item.event.duration_hours}h</b><button class="button soft" data-event="${item.event.event_id}">Details</button></div></article>`}
function skillBar(skill){const pct=Math.min(100,100*skill.current/Math.max(skill.target,1));return `<div class="skill-item"><div><span>${esc(skill.name)}${skill.critical?'<i>critical</i>':''}</span><b>${skill.current} / ${skill.target}</b></div><div class="skill-track"><i style="width:${pct}%"></i></div></div>`}

function opportunities() {
  const d=state.detail,events=d.personal_events||[];
  let catalog=d.catalog.filter(x=>state.catalogFilter==='all'||(state.catalogFilter==='recommended'?d.recommendations.some(r=>r.event.event_id===x.event_id):x.eligible));
  if(state.query) catalog=catalog.filter(x=>(x.title+' '+x.description).toLowerCase().includes(state.query.toLowerCase()));
  return `<section class="page-intro"><p class="kicker">MAKE BETTER CHOICES</p><h1>Is this opportunity worth your time?</h1><p>Add any conference, course or meetup. Career Quest checks it against your actual plan.</p></section>
  <section class="event-analyzer"><form id="eventForm" class="form-card dark"><span class="section-kicker">AI OPPORTUNITY CHECK</span><h2>Analyse an upcoming event</h2><label>Event name<input name="title" required maxlength="160" placeholder="AI Engineering Summit"></label><label>What will it cover?<textarea name="description" required minlength="10" maxlength="1600" placeholder="Paste the agenda or describe the sessions…"></textarea></label><div class="form-grid three"><label>Date<input name="date" type="date" required value="${new Date().toISOString().slice(0,10)}"></label><label>Hours<input name="hours" type="number" min="1" max="200" value="4" required></label><label>Format<select name="format"><option>online</option><option>offline</option><option>hybrid</option><option>self_paced</option></select></label></div><button class="button light wide" type="submit">✦ Analyse career fit</button></form>
    <div class="analysis-stack"><div class="section-head"><div><span class="section-kicker">SAVED CHECKS</span><h2>Your opportunity decisions</h2></div></div><p class="data-note">AI analysis uses your career goal, skill gaps and the event description. Your photo and email are never included.</p>${events.length?events.slice(0,4).map(eventAnalysis).join(''):'<div class="empty-state tall"><b>No checks yet</b><span>Your first event analysis will appear here.</span></div>'}</div></section>
  <section class="surface catalog-surface"><div class="section-head"><div><span class="section-kicker">CURATED FOR YOUR PATH</span><h2>Explore company opportunities</h2></div><input id="catalogSearch" class="compact-input" placeholder="Search" value="${esc(state.query)}"></div><div class="segmented">${[['recommended','Recommended'],['available','Available'],['all','All']].map(([id,label])=>`<button data-filter="${id}" class="${state.catalogFilter===id?'active':''}">${label}</button>`).join('')}</div><div class="opportunity-grid">${catalog.slice(0,12).map(opportunityCard).join('')||'<div class="empty-state">No matching opportunities.</div>'}</div></section>`;
}

function eventAnalysis(event){const a=event.analysis;return `<article class="analysis-card"><div class="score-ring ${a.score<50?'low':''}"><b>${a.score}</b><span>fit</span></div><div><span>${dateLabel(event.date)} · ${event.hours}h</span><h3>${esc(event.title)}</h3><p>${esc(a.summary)}</p><div class="skill-pills">${a.matched_skills.map(x=>`<i>${esc(x)}</i>`).join('')}</div></div></article>`}
function opportunityCard(event){return `<article class="opportunity-card"><div><span>${esc(human(event.type))}</span><b class="tiny-badge">${event.completed?'Done':event.eligible?'Open':'Locked'}</b></div><h3>${esc(event.title)}</h3><p>${esc(event.description)}</p><footer><span>${event.duration_hours}h · ${esc(human(event.format))}</span><button data-event="${event.event_id}">View →</button></footer></article>`}

function activity() {
  const d=state.detail,map=Object.fromEntries(d.catalog.map(x=>[x.event_id,x]));
  return `<section class="page-intro"><p class="kicker">YOUR MOMENTUM</p><h1>Every step counts.</h1><p>A private record of the work you are doing for your future.</p></section><section class="metric-grid activity-metrics">${metric('Path readiness',`${d.readiness}%`,'toward your goal','◎')}${metric('Learning records',d.history.length,'all time','✓')}${metric('Skill gains',d.applied_since_review.length,'since last review','↗')}${metric('Level',d.path_level.level,d.path_level.name,'✦')}</section>
    <section class="surface"><div class="section-head"><div><span class="section-kicker">TIMELINE</span><h2>Your growth history</h2></div></div><div class="timeline">${d.history.map(row=>`<article><i class="${row.status==='completed'?'done':''}">${row.status==='completed'?'✓':'·'}</i><div><h3>${esc(map[row.event_id]?.title||row.event_id)}</h3><p>${human(row.status)} · ${Number(row.completion_pct)}% complete</p></div><time>${dateLabel(row.date)}</time></article>`).join('')}</div></section>`;
}

function profile() {
  const e=state.detail.employee;
  return `<section class="page-intro"><p class="kicker">YOUR STORY</p><h1>Make this space yours.</h1><p>Your profile helps Career Quest understand the person behind the skills.</p></section><div class="profile-layout">
    <form id="profileForm" class="form-card profile-form"><div class="profile-photo-row"><div id="photoPreview">${state.photoDraft?`<img class="avatar xlarge" src="${esc(state.photoDraft)}" alt="Profile preview">`:avatar(e,'xlarge')}</div><div><h2>${esc(e.full_name)}</h2><p>${esc(e.grade)} ${esc(e.role)}</p><label class="button soft upload-button">Choose photo<input id="photoInput" type="file" accept="image/png,image/jpeg,image/webp"></label></div></div>
      <label>Full name<input name="full_name" required maxlength="120" value="${esc(e.full_name)}"></label><label>Short bio<textarea name="bio" maxlength="800" placeholder="What motivates you? What kind of work gives you energy?">${esc(e.bio||'')}</textarea></label>
      <div class="form-grid two"><label>Email<input name="email" type="email" value="${esc(e.email||'')}" placeholder="you@company.com"></label><label>Work style<select name="work_format">${['office','hybrid','remote'].map(x=>`<option ${x===e.work_format?'selected':''}>${x}</option>`).join('')}</select></label></div>
      <label class="switch-row"><span><b>Email nudges</b><small>Weekly plan reminder and relevant event suggestions</small></span><input name="email_updates" type="checkbox" ${e.email_updates?'checked':''}><i></i></label>
      <button class="button primary" type="submit">Save profile</button>${state.user.email_available&&e.email?'<button class="button soft" id="emailTest" type="button">Send connection test</button>':'<p class="data-note">Your address can be saved now. Email delivery starts when an SMTP provider is added to the server.</p>'}
    </form>
    <aside class="surface identity-card"><span class="section-kicker">CAREER IDENTITY</span><h2>${esc(targetLabel())}</h2><p>${esc(e.career_goal?.statement||'Your written goal will appear here.')}</p><div class="identity-facts"><div><span>Department</span><b>${esc(e.department)}</b></div><div><span>Work style</span><b>${human(e.work_format)}</b></div><div><span>Email</span><b>${state.user.email_available&&e.email?'Delivery ready':e.email?'Address saved':'Not connected'}</b></div><div><span>Privacy</span><b>Personal + HR</b></div></div><button class="button soft wide" data-page="plan">Edit career goal</button></aside>
  </div>`;
}

function team() {
  if(!state.hr) return '<div class="boot"><div class="boot-mark">✦</div><span>Building the team view…</span></div>';
  return `<section class="page-intro"><p class="kicker">PEOPLE & GROWTH</p><h1>Help people find momentum.</h1><p>Create accounts, understand shared gaps and support meaningful development.</p></section>
    <section class="metric-grid">${metric('People',state.hr.totals.people,'active profiles','●')}${metric('With next steps',state.hr.totals.with_steps,'plans ready','✓')}${metric('Completed',state.hr.totals.completed,'learning records','↗')}${metric('Needs a plan',state.hr.totals.people-state.hr.totals.with_steps,'start a conversation','◎')}</section>
    <div class="team-layout"><form id="accountForm" class="form-card"><span class="section-kicker">NEW EMPLOYEE</span><h2>Create profile + account</h2><div class="form-grid two"><label>Full name<input name="full_name" required></label><label>Email<input name="email" type="email" required></label></div><label>Department<input name="department" required list="teamDepartments"><datalist id="teamDepartments">${state.departments.map(x=>`<option value="${esc(x)}">`).join('')}</datalist></label><div class="form-grid two"><label>Role<select name="role">${state.roles.map(x=>`<option>${esc(x)}</option>`).join('')}</select></label><label>Level<select name="grade">${['Junior','Middle','Senior','Lead'].map(x=>`<option>${x}</option>`).join('')}</select></label></div><div class="form-grid two"><label>Username<input name="username" minlength="3" required></label><label>Temporary password<input name="password" type="password" minlength="10" required></label></div><button class="button primary wide" type="submit">Create employee account</button></form>
      <section class="surface people-list"><div class="section-head"><div><span class="section-kicker">TEAM</span><h2>Individual paths</h2></div></div>${state.hr.employees.slice(0,16).map(personRow).join('')}</section></div>`;
}

function personRow(person){return `<button class="person-row" data-person="${person.employee_id}"><span class="avatar">${initials(person.full_name)}</span><span><b>${esc(person.full_name)}</b><small>${esc(person.grade)} ${esc(person.role)}</small></span><div><b>${person.readiness}%</b><small>ready</small></div><i>›</i></button>`}

function openModal(content){const layer=document.getElementById('modal');layer.hidden=false;layer.classList.add('open');document.getElementById('modalContent').innerHTML=content;document.getElementById('closeModal').focus();wireModal()}
function closeModal(){const layer=document.getElementById('modal');layer?.classList.remove('open');if(layer)layer.hidden=true}
function showEvent(id){const event=state.detail.catalog.find(x=>x.event_id===id);if(!event)return;openModal(`<span class="section-kicker">${esc(human(event.type))} · ${event.duration_hours} HOURS</span><h2>${esc(event.title)}</h2><p>${esc(event.description)}</p><div class="modal-skills">${event.effects.map(x=>`<div><span>${esc(x.name)}</span><b>${x.before} → ${x.after}</b></div>`).join('')}</div>${event.blocked_reasons.length?`<div class="warning-note">${event.blocked_reasons.map(esc).join('. ')}</div>`:`<button id="completeEvent" data-id="${event.event_id}" class="button primary wide">Record completion</button>`}`)}

function wireModal(){document.getElementById('closeModal').onclick=closeModal;document.getElementById('modal').onclick=e=>{if(e.target.id==='modal')closeModal()};document.getElementById('completeEvent')?.addEventListener('click',async e=>{e.currentTarget.disabled=true;try{const result=await api(`/api/employees/${state.selected}/activities/${e.currentTarget.dataset.id}/complete`,{method:'POST'});closeModal();await loadProfile();toast(result.already_completed?'Already completed.':'Completion saved. Your path moved forward.')}catch(err){toast(err.message,'bad');e.currentTarget.disabled=false}})}

function wire() {
  document.querySelectorAll('[data-page]').forEach(x=>x.onclick=()=>navigate(x.dataset.page));
  document.getElementById('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'});}finally{state.user=null;state.detail=null;showLogin()}};
  document.getElementById('employeeSelect')?.addEventListener('change',async e=>{state.selected=e.target.value;state.page='today';await loadProfile()});
  document.querySelectorAll('[data-person]').forEach(x=>x.onclick=async()=>{state.selected=x.dataset.person;state.page='today';await loadProfile()});
  document.querySelectorAll('[data-event]').forEach(x=>x.onclick=()=>showEvent(x.dataset.event));
  document.getElementById('closeModal')?.addEventListener('click',closeModal);
  document.getElementById('generateAI')?.addEventListener('click',generatePlan);
  document.querySelectorAll('[data-filter]').forEach(x=>x.onclick=()=>{state.catalogFilter=x.dataset.filter;render()});
  document.getElementById('catalogSearch')?.addEventListener('input',e=>{state.query=e.target.value;const pos=e.target.selectionStart;render();const input=document.getElementById('catalogSearch');input.focus();input.setSelectionRange(pos,pos)});
  const hours=document.querySelector('[name=weekly_hours]');hours?.addEventListener('input',()=>document.getElementById('hoursValue').textContent=`${hours.value} hours`);
  document.getElementById('goalForm')?.addEventListener('submit',saveGoal);
  document.getElementById('profileForm')?.addEventListener('submit',saveProfile);
  document.getElementById('photoInput')?.addEventListener('change',selectPhoto);
  document.getElementById('eventForm')?.addEventListener('submit',analyseEvent);
  document.getElementById('accountForm')?.addEventListener('submit',createAccount);
  document.getElementById('emailTest')?.addEventListener('click',sendEmailTest);
}

async function generatePlan(){const version=state.version;state.aiBusy=true;render();try{const result=await api(`/api/employees/${state.selected}/recommendations`,{method:'POST'});if(version===state.version){state.plan=result;state.aiBusy=false;render();toast(result.source==='openai'?'Your AI plan is ready.':'Your verified skill plan is ready.')}}catch(e){state.aiBusy=false;render();toast(e.message,'bad')}}
async function saveGoal(event){event.preventDefault();const button=event.currentTarget.querySelector('[type=submit]');button.disabled=true;try{const data=Object.fromEntries(new FormData(event.currentTarget));data.weekly_hours=Number(data.weekly_hours);await api(`/api/employees/${state.selected}/goal`,{method:'PUT',body:JSON.stringify(data)});await loadProfile();state.page='plan';render();toast('Direction saved. Your plan has been recalculated.')}catch(e){toast(e.message,'bad');button.disabled=false}}
async function saveProfile(event){event.preventDefault();const values=Object.fromEntries(new FormData(event.currentTarget));values.email_updates=event.currentTarget.email_updates.checked;values.photo=state.photoDraft;const button=event.currentTarget.querySelector('[type=submit]');button.disabled=true;try{await api(`/api/employees/${state.selected}/profile`,{method:'PATCH',body:JSON.stringify(values)});await refreshBootstrap();await loadProfile();state.page='profile';render();toast('Profile saved.')}catch(e){toast(e.message,'bad');button.disabled=false}}
async function selectPhoto(event){const file=event.target.files[0];if(!file)return;try{state.photoDraft=await resizeImage(file);document.getElementById('photoPreview').innerHTML=`<img class="avatar xlarge" src="${state.photoDraft}" alt="Profile preview">`}catch(e){toast(e.message,'bad')}}
function resizeImage(file){return new Promise((resolve,reject)=>{if(!['image/jpeg','image/png','image/webp'].includes(file.type))return reject(new Error('Choose a JPG, PNG or WebP image.'));const image=new Image(),reader=new FileReader();reader.onload=()=>image.src=reader.result;reader.onerror=()=>reject(new Error('Could not read the image.'));image.onload=()=>{const scale=Math.min(1,512/Math.max(image.width,image.height)),canvas=document.createElement('canvas');canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);resolve(canvas.toDataURL('image/jpeg',.84))};reader.readAsDataURL(file)})}
async function analyseEvent(event){event.preventDefault();const values=Object.fromEntries(new FormData(event.currentTarget));values.hours=Number(values.hours);const button=event.currentTarget.querySelector('[type=submit]');button.disabled=true;button.textContent='✦ Analysing your fit…';try{const result=await api(`/api/employees/${state.selected}/event-analysis`,{method:'POST',body:JSON.stringify(values)});state.detail.personal_events.unshift(result);render();toast(`Career fit: ${result.analysis.score}/100`)}catch(e){toast(e.message,'bad');button.disabled=false;button.textContent='✦ Analyse career fit'}}
async function createAccount(event){event.preventDefault();const values=Object.fromEntries(new FormData(event.currentTarget));const button=event.currentTarget.querySelector('[type=submit]');button.disabled=true;try{const result=await api('/api/hr/employees',{method:'POST',body:JSON.stringify(values)});await refreshBootstrap();state.hr=await api('/api/hr/summary');render();toast(`Account created for ${result.employee.full_name}.`)}catch(e){toast(e.message,'bad');button.disabled=false}}
async function sendEmailTest(event){event.currentTarget.disabled=true;try{await api(`/api/employees/${state.selected}/email-test`,{method:'POST'});toast('Test email sent.')}catch(e){toast(e.message,'bad');event.currentTarget.disabled=false}}

try { state.user=await api('/api/auth/me'); await start(); } catch { showLogin(); }
