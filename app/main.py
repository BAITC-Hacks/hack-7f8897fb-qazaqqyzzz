import csv
import io
import json
import secrets
import smtplib
import time
import uuid
from typing import Literal
from collections import Counter, defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import settings as default_settings
from .database import Store
from .security import password_hash, verify_password, token_hash
from .engine import evaluate, validate_profiles, validate_history, RECURRING, MISSED
from .ai import Planner
from .email import Mailer

class Login(BaseModel):
    username: str = Field(min_length=1,max_length=100)
    password: str = Field(min_length=1,max_length=200)

class ImportFile(BaseModel):
    filename: str = Field(max_length=200)
    content: str = Field(max_length=6_000_000)

class DemoLogin(BaseModel):
    role: Literal['employee','hr']

class Register(BaseModel):
    username: str = Field(min_length=3,max_length=40,pattern=r'^[A-Za-z0-9._-]+$')
    password: str = Field(min_length=10,max_length=200)
    full_name: str = Field(min_length=2,max_length=120)
    email: str = Field(min_length=5,max_length=200)
    department: str = Field(min_length=2,max_length=120)
    role: str = Field(min_length=2,max_length=120)
    grade: Literal['Junior','Middle','Senior','Lead']

class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=2,max_length=120)
    bio: str = Field(default='',max_length=800)
    email: str = Field(default='',max_length=200)
    email_updates: bool = False
    photo: str = Field(default='',max_length=1_600_000)
    work_format: Literal['office','remote','hybrid'] = 'hybrid'

class GoalUpdate(BaseModel):
    target_role: str = Field(min_length=2,max_length=120)
    target_grade: Literal['Junior','Middle','Senior','Lead']
    statement: str = Field(min_length=15,max_length=1200)
    timeline: str = Field(default='6 months',max_length=80)
    weekly_hours: int = Field(default=3,ge=1,le=30)

class EventProposal(BaseModel):
    title: str = Field(min_length=3,max_length=160)
    description: str = Field(min_length=10,max_length=1600)
    date: str = Field(min_length=10,max_length=10)
    hours: int = Field(ge=1,le=200)
    format: Literal['online','offline','hybrid','self_paced'] = 'online'

class NewEmployee(Register):
    employee_id: str | None = Field(default=None,max_length=40,pattern=r'^[A-Za-z0-9_-]+$')

def create_app(settings=default_settings):
    store = Store(settings)
    planner = Planner(settings,store)
    mailer = Mailer(settings)
    attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        if settings.production and not settings.origin.startswith('https://'):
            raise RuntimeError('PUBLIC_ORIGIN must be the public HTTPS URL in production.')
        store.initialize()
        yield

    app = FastAPI(title='Career Quest API', version='2.0.0', lifespan=lifespan,
                  docs_url=None if settings.production else '/docs', redoc_url=None)
    app.state.store, app.state.planner, app.state.mailer = store, planner, mailer

    def rate_limit(key, limit, window):
        now = time.time()
        if len(attempts)>5000:
            for k in list(attempts):
                if not attempts[k] or attempts[k][-1]<now-900:
                    del attempts[k]
        queue = attempts[key]
        while queue and queue[0] < now-window:
            queue.popleft()
        if len(queue)>=limit:
            raise HTTPException(429,'Too many attempts. Please try again later.')
        queue.append(now)

    @app.middleware('http')
    async def guard(request, call_next):
        if request.method in ('POST','PUT','PATCH','DELETE'):
            origin = request.headers.get('origin')
            expected = settings.origin or str(request.base_url).rstrip('/')
            if origin and origin.rstrip('/') != expected:
                return JSONResponse({'detail':'Cross-origin changes are not allowed.'},status_code=403)
            try:
                length=int(request.headers.get('content-length','0'))
            except ValueError:
                return JSONResponse({'detail':'Invalid request length.'},status_code=400)
            if length>8_000_000:
                return JSONResponse({'detail':'File is too large (8 MB limit).'},status_code=413)
            if len(await request.body())>8_000_000:
                return JSONResponse({'detail':'File is too large (8 MB limit).'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    def session(request: Request):
        raw = request.cookies.get('cq_session','')
        with store.connect() as c:
            row=c.execute('SELECT users.username,users.role,users.employee_id,sessions.csrf FROM sessions JOIN users USING(username) WHERE token_hash=? AND expires>?',
                          (token_hash(raw),time.time())).fetchone()
        if not row:
            raise HTTPException(401,'Please sign in.')
        user=dict(row)
        if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),user['csrf']):
            raise HTTPException(403,'Refresh the page before making changes.')
        return user

    def hr(user=Depends(session)):
        if user['role']!='hr':
            raise HTTPException(403,'This view is available to HR only.')
        return user

    def employee(eid,user,data):
        if user['role']!='hr' and eid!=user['employee_id']:
            raise HTTPException(403,'You can only access your own profile.')
        item=next((e for e in data['employees'] if e['employee_id']==eid),None)
        if not item:
            raise HTTPException(404,'Employee not found.')
        return item

    def local_demo(request):
        return (not settings.production and request.url.hostname in {'localhost','127.0.0.1','::1'}
                and request.client and request.client.host in {'127.0.0.1','::1'})

    def valid_email(value):
        return not value or ('@' in value and '.' in value.rsplit('@',1)[-1] and ' ' not in value)

    def create_employee_account(c, data, body, actor):
        username=body.username.lower()
        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
            raise HTTPException(409,'That username is already in use.')
        if not valid_email(body.email):
            raise HTTPException(422,'Enter a valid email address.')
        if (body.role,body.grade) not in {(p['role'],p['grade']) for p in data['role_profiles']}:
            raise HTTPException(422,'Choose a role and grade from the career catalog.')
        if any(body.email.lower()==e.get('email','').lower() for e in data['employees'] if body.email):
            raise HTTPException(409,'That email is already connected to an account.')
        requested=getattr(body,'employee_id',None)
        eid=requested or 'EUSER_'+uuid.uuid4().hex[:8].upper()
        if any(e['employee_id']==eid for e in data['employees']):
            raise HTTPException(409,'That employee ID already exists.')
        employee={'employee_id':eid,'full_name':body.full_name.strip(),'department':body.department.strip(),
                  'role':body.role,'grade':body.grade,'manager_id':'','hire_date':data['as_of_date'],
                  'tenure_months':0,'work_format':'hybrid','preferred_language':'en','email':body.email.strip(),
                  'email_updates':False,'bio':'','photo':'','career_goal':None,'skills':{},
                  'last_review_date':data['as_of_date']}
        people=[*data['employees'],employee]
        store.put(c,'employees',{'meta':{'as_of_date':data['as_of_date']},'employees':people})
        c.execute('INSERT INTO users VALUES (?,?,?,?)',(username,password_hash(body.password),'employee',eid))
        c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',(actor,'create_account',eid))
        return employee,username

    @app.get('/api/config')
    def config(request:Request):
        return {'local_demo':bool(local_demo(request))}

    @app.get('/api/catalog')
    def public_catalog():
        data=store.snapshot()
        return {'role_profiles':[{'role':p['role'],'grade':p['grade']} for p in data['role_profiles']],
                'departments':sorted({e['department'] for e in data['employees']})}

    @app.post('/api/auth/demo')
    def demo(body:DemoLogin,request:Request):
        if not local_demo(request):
            raise HTTPException(404,'Not found.')
        username='admin' if body.role=='hr' else 'employee'
        raw,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(24)
        with store.connect(write=True) as c:
            row=c.execute('SELECT username,role,employee_id FROM users WHERE username=?',(username,)).fetchone()
            if not row:
                raise HTTPException(404,'Demo account is not available.')
            c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(token_hash(raw),username,csrf,time.time()+28800))
        response=JSONResponse({**dict(row),'csrf':csrf})
        response.set_cookie('cq_session',raw,httponly=True,samesite='strict',max_age=28800,path='/')
        return response

    @app.post('/api/auth/register')
    def register(body:Register,request:Request):
        rate_limit(('register',request.client.host if request.client else 'unknown'),5,3600)
        with store.connect(write=True) as c:
            employee_row,username=create_employee_account(c,store.snapshot(c),body,'self')
            raw,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(24)
            c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(token_hash(raw),username,csrf,time.time()+28800))
        response=JSONResponse({'username':username,'role':'employee','employee_id':employee_row['employee_id'],'csrf':csrf})
        response.set_cookie('cq_session',raw,httponly=True,secure=settings.production,samesite='strict',max_age=28800,path='/')
        return response

    @app.get('/healthz')
    def health():
        with store.connect() as c:
            c.execute('SELECT 1 FROM documents LIMIT 1').fetchone()
        return {'status':'ok'}

    @app.post('/api/auth/login')
    def login(body:Login,request:Request):
        rate_limit(('login',request.client.host if request.client else 'unknown'),10,900)
        with store.connect(write=True) as c:
            row=c.execute('SELECT * FROM users WHERE username=?',(body.username,)).fetchone()
            if not row or not verify_password(body.password,row['password']):
                raise HTTPException(401,'Incorrect username or password.')
            raw=secrets.token_urlsafe(32)
            csrf=secrets.token_urlsafe(24)
            c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(token_hash(raw),row['username'],csrf,time.time()+28800))
        response=JSONResponse({'username':row['username'],'role':row['role'],'employee_id':row['employee_id'],'csrf':csrf})
        response.set_cookie('cq_session',raw,httponly=True,secure=settings.production,samesite='strict',max_age=28800,path='/')
        return response

    @app.get('/api/auth/me')
    def me(user=Depends(session)):
        return {**user,'ai_available':bool(settings.api_key),'model':settings.model if settings.api_key else None,
                'email_available':mailer.configured}

    @app.post('/api/auth/logout')
    def logout(request:Request,user=Depends(session)):
        with store.connect(write=True) as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(request.cookies.get('cq_session','')),))
        response=JSONResponse({'ok':True})
        response.delete_cookie('cq_session',path='/')
        return response

    @app.get('/api/bootstrap')
    def bootstrap(user=Depends(session)):
        data=store.snapshot()
        people=data['employees'] if user['role']=='hr' else [e for e in data['employees'] if e['employee_id']==user['employee_id']]
        return {'employees':[{k:e[k] for k in ('employee_id','full_name','role','grade','department')} for e in people],
                'skills':data['skills'],'roles':sorted({p['role'] for p in data['role_profiles']}),
                'role_profiles':[{'role':p['role'],'grade':p['grade']} for p in data['role_profiles']],
                'departments':sorted({e['department'] for e in data['employees']}),'as_of_date':data['as_of_date']}

    @app.get('/api/employees/{eid}')
    def detail(eid:str,user=Depends(session)):
        data=store.snapshot()
        result=evaluate(employee(eid,user,data),data)
        result.pop('candidates')
        return result

    @app.post('/api/employees/{eid}/recommendations')
    async def recommendations(eid:str,user=Depends(session)):
        rate_limit(('ai',user['username']),15,60)
        data=store.snapshot()
        result=evaluate(employee(eid,user,data),data)
        return await planner.plan(result)

    @app.patch('/api/employees/{eid}/profile')
    def update_profile(eid:str,body:ProfileUpdate,user=Depends(session)):
        if not valid_email(body.email):
            raise HTTPException(422,'Enter a valid email address.')
        if body.photo and not body.photo.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,')):
            raise HTTPException(422,'Upload a JPG, PNG or WebP image.')
        with store.connect(write=True) as c:
            data=store.snapshot(c); current=employee(eid,user,data)
            if body.email and any(body.email.lower()==x.get('email','').lower() and x['employee_id']!=eid for x in data['employees']):
                raise HTTPException(409,'That email is already connected to another profile.')
            updated={**current,**body.model_dump()}
            people=[updated if x['employee_id']==eid else x for x in data['employees']]
            store.put(c,'employees',{'meta':{'as_of_date':data['as_of_date']},'employees':people})
            c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',(user['username'],'update_profile',eid))
        return {'saved':True,'employee':updated}

    @app.post('/api/employees/{eid}/email-test')
    def email_test(eid:str,user=Depends(session)):
        rate_limit(('email',user['username']),3,3600)
        data=store.snapshot(); current=employee(eid,user,data)
        if not current.get('email'):
            raise HTTPException(422,'Save an email address first.')
        if not mailer.configured:
            raise HTTPException(503,'Email delivery is not configured on this server yet.')
        try:
            mailer.send_test(current['email'],current['full_name'].split()[0])
        except (OSError,smtplib.SMTPException) as ex:
            raise HTTPException(502,'The mail provider could not deliver the test message.') from ex
        return {'sent':True}

    @app.put('/api/employees/{eid}/goal')
    def update_goal(eid:str,body:GoalUpdate,user=Depends(session)):
        with store.connect(write=True) as c:
            data=store.snapshot(c); current=employee(eid,user,data)
            if (body.target_role,body.target_grade) not in {(p['role'],p['grade']) for p in data['role_profiles']}:
                raise HTTPException(422,'Choose a valid target role and grade.')
            updated={**current,'career_goal':body.model_dump()}
            people=[updated if x['employee_id']==eid else x for x in data['employees']]
            store.put(c,'employees',{'meta':{'as_of_date':data['as_of_date']},'employees':people})
            c.execute('DELETE FROM ai_cache')
            c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',(user['username'],'update_goal',eid))
        return {'saved':True,'career_goal':updated['career_goal']}

    @app.post('/api/employees/{eid}/event-analysis')
    async def analyze_personal_event(eid:str,body:EventProposal,user=Depends(session)):
        rate_limit(('event_ai',user['username']),10,60)
        try:
            from datetime import date
            date.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(422,'Enter a valid event date.') from None
        data=store.snapshot(); current=employee(eid,user,data); detail_result=evaluate(current,data)
        proposal=body.model_dump(); analysis=await planner.analyze_event(detail_result,proposal)
        saved={'event_id':'PERSONAL_'+uuid.uuid4().hex[:12].upper(),'employee_id':eid,**proposal,
               'analysis':analysis,'status':'planned','created_at':int(time.time())}
        with store.connect(write=True) as c:
            c.execute('INSERT INTO personal_events(event_id,employee_id,payload) VALUES (?,?,?)',
                      (saved['event_id'],eid,json.dumps(saved)))
            c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',(user['username'],'analyze_event',saved['event_id']))
        return saved

    @app.post('/api/employees/{eid}/activities/{event_id}/complete')
    def complete(eid:str,event_id:str,user=Depends(session)):
        with store.connect(write=True) as c:
            data=store.snapshot(c)
            e=employee(eid,user,data)
            before=evaluate(e,data)
            ev=next((x for x in before['catalog'] if x['event_id']==event_id),None)
            if not ev:
                raise HTTPException(404,'Activity not found.')
            done=[r for r in before['history'] if r['event_id']==event_id and r['status']=='completed']
            if done and (event_id not in RECURRING or any(r['date']==data['as_of_date'] for r in done)):
                return {'already_completed':True,'changes':[],'readiness':before['readiness']}
            if not ev['eligible']:
                raise HTTPException(409,'; '.join(ev['blocked_reasons']))
            row={'record_id':'APP_'+uuid.uuid4().hex,'employee_id':eid,'event_id':event_id,'date':data['as_of_date'],
                 'due_date':'','status':'completed','completion_pct':'100','score':'','feedback_rating':'','assigned_by':'self',
             '_app_completion':True}
            store.put_record(c,row)
            c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',(user['username'],'complete',f'{eid}:{event_id}'))
            data['history'].append(row)
            after=evaluate(e,data)
        changes=[{'skill_id':sid,'before':old,'after':after['effective_skills'].get(sid,0)}
                 for sid,old in {**{g['skill_id']:0 for g in ev['develops_skills']},**before['effective_skills']}.items()
                 if after['effective_skills'].get(sid,0)>old]
        return {'already_completed':False,'changes':changes,'readiness':after['readiness']}

    @app.get('/api/hr/summary')
    def summary(user=Depends(hr)):
        data=store.snapshot()
        gaps=Counter()
        people=[]
        for e in data['employees']:
            d=evaluate(e,data)
            for s in d['skill_progress']:
                if s['gap']:
                    gaps[s['name']]+=1
            people.append({**{k:e[k] for k in ('employee_id','full_name','role','grade','department')},
                           'readiness':d['readiness'],'next_step':d['recommendations'][0]['event']['title'] if d['recommendations'] else None,
                           'reason':d['no_recommendation_reason']})
        participation=[]
        for ev in data['events']:
            rows=[r for r in data['history'] if r['event_id']==ev['event_id']]
            participation.append({'event_id':ev['event_id'],'title':ev['title'],'mandatory':ev['mandatory'],
                                  'total':len(rows),'completed':sum(r['status']=='completed' for r in rows),
                                  'missed':sum(r['status'] in MISSED for r in rows)})
        return {'employees':people,'gaps':[{'name':name,'people':n} for name,n in gaps.most_common(12)],
                'participation':sorted(participation,key=lambda x:-x['total']),
                'totals':{'people':len(people),'with_steps':sum(bool(p['next_step']) for p in people),
                          'completed':sum(r['status']=='completed' for r in data['history']),
                          'missed':sum(r['status'] in MISSED for r in data['history'])}}

    @app.post('/api/hr/employees')
    def create_employee(body:NewEmployee,user=Depends(hr)):
        with store.connect(write=True) as c:
            employee_row,username=create_employee_account(c,store.snapshot(c),body,user['username'])
        return {'created':True,'username':username,'employee':employee_row}

    @app.post('/api/hr/import')
    def import_file(body:ImportFile,user=Depends(hr)):
        try:
            with store.connect(write=True) as c:
                data=store.snapshot(c)
                if body.filename.lower().endswith('.json'):
                    parsed=json.loads(body.content)
                    items=parsed if isinstance(parsed,list) else parsed.get('employees',[parsed])
                    validate_profiles(items,data)
                    people={e['employee_id']:e for e in data['employees']}
                    for e in items:
                        people[e['employee_id']]=e
                    store.put(c,'employees',{'meta':{'as_of_date':data['as_of_date']},'employees':list(people.values())})
                    kind='profiles'
                elif body.filename.lower().endswith('.csv'):
                    items=list(csv.DictReader(io.StringIO(body.content.lstrip('\ufeff'))))
                    validate_history(items,data)
                    for row in items:
                        store.put_record(c,row)
                    kind='history records'
                else:
                    raise ValueError('Choose employee JSON or activity-history CSV.')
                c.execute('DELETE FROM ai_cache')
                c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',(user['username'],'import',f'{len(items)} {kind}'))
        except (ValueError,TypeError,KeyError,AttributeError) as ex:
            raise HTTPException(422,str(ex)[:300]) from None
        return {'imported':len(items),'kind':kind}

    @app.get('/api/hr/export')
    def export(user=Depends(hr)):
        return JSONResponse(store.snapshot(),headers={'Content-Disposition':'attachment; filename="career-quest-export.json"'})

    @app.get('/')
    def index():
        return FileResponse(settings.root/'index.html',headers={'Cache-Control':'no-cache'})

    # Only UI assets are public; dataset, database, secrets and source stay private.
    app.mount('/src',StaticFiles(directory=settings.root/'src'),name='assets')
    return app

app=create_app()
