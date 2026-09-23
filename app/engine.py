"""Evidence-driven eligibility and progression. An LLM cannot override these rules."""
from datetime import date

GRADES = ['Junior', 'Middle', 'Senior', 'Lead']
MISSED = {'no_show', 'dropped', 'declined', 'overdue'}
RECURRING = {'EV_036'}

def effective_skills(employee, history, events, as_of):
    levels = dict(employee.get('skills', {}))
    applied = []
    for row in sorted(history, key=lambda r: (r['date'], r['record_id'])):
        if row['employee_id'] != employee['employee_id'] or row['status'] != 'completed':
            continue
        # Imported history after the last review is applied chronologically. App-created
        # completions are marked explicitly so a completion on the snapshot date counts.
        if row.get('_app_completion'):
            if row['date'] != as_of:
                continue
        elif not employee['last_review_date'] < row['date'] <= as_of:
            continue
        for effect in events.get(row['event_id'], {}).get('develops_skills', []):
            sid = effect['skill_id']
            before = levels.get(sid, 0)
            after = max(before, min(5, effect['max_level'], before + effect['gain']))
            levels[sid] = after
            if after > before:
                applied.append({'skill_id': sid, 'before': before, 'after': after, 'record_id': row['record_id']})
    return levels, applied

def target_profile(e, profiles):
    goal = e.get('career_goal')
    if goal:
        role, grade = goal['target_role'], goal['target_grade']
    else:
        role = e['role']
        grade = GRADES[min(GRADES.index(e['grade']) + 1, 3)]
    return next((p for p in profiles if p['role'] == role and p['grade'] == grade), None)

def evaluate(e, data):
    event_map = {ev['event_id']: ev for ev in data['events']}
    names = {s['skill_id']: s['name'] for s in data['skills']}
    history = [r for r in data['history'] if r['employee_id'] == e['employee_id']]
    levels, applied = effective_skills(e, history, event_map, data['as_of_date'])
    target = target_profile(e, data['role_profiles'])
    req = target['required_skills'] if target else {}
    critical = set(target.get('critical_skills', [])) if target else set()
    skill_rows = [{'skill_id': sid, 'name': names.get(sid, sid), 'current': levels.get(sid, 0),
                   'target': minimum, 'gap': max(0, minimum-levels.get(sid, 0)), 'critical': sid in critical}
                  for sid, minimum in req.items()]
    skill_rows.sort(key=lambda s: (-s['critical'], -s['gap'], s['name']))
    gaps = {s['skill_id']: s for s in skill_rows if s['gap'] > 0}
    earned = sum(min(s['current'], s['target']) for s in skill_rows)
    possible = sum(s['target'] for s in skill_rows)
    completed = {r['event_id'] for r in history if r['status'] == 'completed'}
    catalog, candidates = [], []
    for ev in data['events']:
        eid = ev['event_id']
        blocked = []
        if e['role'] not in ev['target_roles'] or e['grade'] not in ev['target_grades']:
            blocked.append('Audience does not match your current role and grade')
        if eid in completed and eid not in RECURRING:
            blocked.append('Already completed')
        if eid in RECURRING and any(r['event_id']==eid and r['status']=='completed' and r['date']==data['as_of_date'] for r in history):
            blocked.append('Already recorded this recurring activity today')
        for sid, required in ev.get('prerequisites', {}).items():
            if levels.get(sid, 0) < required:
                blocked.append(f'{names.get(sid, sid)} needs level {required} to join')
        upcoming = [d for d in ev.get('upcoming_sessions', []) if d >= data['as_of_date']]
        if ev['format'] != 'self_paced' and not upcoming and not any(r['event_id']==eid and r['status']=='in_progress' for r in history):
            blocked.append('No upcoming session available')
        effects = []
        for g in ev['develops_skills']:
            current = levels.get(g['skill_id'], 0)
            after = max(current, min(5, g['max_level'], current+g['gain']))
            effects.append({'skill_id': g['skill_id'], 'name': names.get(g['skill_id'], g['skill_id']),
                            'before': current, 'after': after, 'gain': after-current,
                            'target': req.get(g['skill_id']), 'critical': g['skill_id'] in critical})
        entry = {**ev, 'eligible': not blocked, 'blocked_reasons': blocked, 'effects': effects,
                 'completed': eid in completed, 'next_session': upcoming[0] if upcoming else None}
        catalog.append(entry)
        if ev['mandatory'] or blocked:
            continue
        gains = [g for g in effects if g['gain'] > 0 and g['skill_id'] in gaps]
        if not gains:
            continue
        developed = {g['skill_id'] for g in gains}
        related = [r for r in history if r['event_id']==eid or developed.intersection(
            {s['skill_id'] for s in event_map.get(r['event_id'], {}).get('develops_skills', [])})]
        misses = sum(r['status'] in MISSED for r in related)
        successes = sum(r['status'] == 'completed' for r in related)
        critical_impact = sum(min(g['gain'], gaps[g['skill_id']]['gap']) for g in gains if g['critical'])
        impact = sum(min(g['gain'], gaps[g['skill_id']]['gap'])*(4 if g['critical'] else 1.3) for g in gains)
        same_event_misses = sum(r['event_id']==eid and r['status'] in MISSED for r in history)
        score = impact + min(successes*.15, .6) - min(misses*.35, 1.4) - min(same_event_misses*.8, 2.4)
        if e['work_format'] == 'remote' and ev['format'] == 'offline':
            score -= .4
        best = sorted(gains, key=lambda g: (-g['critical'], -g['gain']))[0]
        history_fact = f'{successes} completed and {misses} missed or unfinished related activities.'
        target_label = f"{target['grade']} {target['role']}" if target else 'your target role'
        factors = [f"Current role: {e['grade']} {e['role']}; target: {target_label}.",
                   f"{best['name']}: {best['before']}/{best['target']} required; this step adds {min(best['gain'], gaps[best['skill_id']]['gap'])} toward the gap.",
                   'This develops a critical target skill.' if critical_impact else 'This develops a supporting target skill.', history_fact]
        candidates.append({'event': entry, 'effects': gains, 'score': round(score, 3),
                           'critical_impact': critical_impact, 'history': {'completed': successes, 'missed': misses},
                           'factors': factors, 'explanation': ' '.join(factors), 'source': 'rules'})
    candidates.sort(key=lambda x: (-bool(x['critical_impact']), -x['score'], x['event']['duration_hours'], x['event']['event_id']))
    completed_count = sum(r['status'] == 'completed' for r in history)
    readiness = round(100*earned/possible) if possible else 0
    xp = readiness * 7 + min(completed_count, 50) * 35 + len(applied) * 20
    level = min(12, 1 + xp // 180)
    level_floor = (level - 1) * 180
    level_progress = 100 if level == 12 else round(100 * (xp - level_floor) / 180)
    level_names = ['Explorer','Builder','Momentum','Specialist','Catalyst','Navigator',
                   'Pathfinder','Mentor','Strategist','Leader','Visionary','Trailblazer']
    return {'employee': e, 'effective_skills': levels, 'applied_since_review': applied,
            'target': target, 'skill_progress': skill_rows,
            'readiness': readiness,
            'path_level': {'level': level, 'name': level_names[level-1], 'xp': xp,
                           'progress': level_progress, 'next_xp': None if level == 12 else level * 180},
            'critical_met': all(s['gap']==0 for s in skill_rows if s['critical']),
            'history': sorted(history, key=lambda r:(r['date'],r['record_id']), reverse=True),
            'catalog': catalog, 'candidates': candidates, 'recommendations': candidates[:3],
            'no_recommendation_reason': None if candidates else ('Target requirements are met; discuss a new goal with your manager.' if not gaps else 'No eligible, uncompleted activity can close this gap. Discuss a learning plan with HR.'),
            'personal_events': [x for x in data.get('personal_events', []) if x.get('employee_id') == e['employee_id']],
            'as_of_date': data['as_of_date']}

def validate_profiles(items, data):
    if not isinstance(items, list) or not items or len(items)>2000:
        raise ValueError('Provide 1–2,000 employee profiles.')
    skills = {s['skill_id'] for s in data['skills']}
    profiles = {(p['role'],p['grade']) for p in data['role_profiles']}
    ids = set()
    for e in items:
        required = ['employee_id','full_name','department','role','grade','skills','last_review_date']
        if not isinstance(e,dict) or any(k not in e for k in required):
            raise ValueError('Each profile requires employee_id, full_name, department, role, grade, skills and last_review_date.')
        if any(not isinstance(e[k],str) or not e[k].strip() or len(e[k])>200 for k in required if k!='skills'):
            raise ValueError('Profile text fields must be nonempty strings of at most 200 characters.')
        if not e['employee_id'].replace('_','').replace('-','').isalnum() or e['employee_id'] in ids:
            raise ValueError('Employee IDs must be unique alphanumeric identifiers.')
        ids.add(e['employee_id'])
        if (e['role'],e['grade']) not in profiles:
            raise ValueError(f"Unknown role/grade for {e['employee_id']}.")
        if not isinstance(e['skills'],dict) or any(s not in skills or type(v) is not int or not 0<=v<=5 for s,v in e['skills'].items()):
            raise ValueError('Skills must use catalog IDs and integer levels 0–5.')
        date.fromisoformat(e['last_review_date'])
        if e['last_review_date']>data['as_of_date']:
            raise ValueError('Review date cannot be later than the dataset snapshot date.')
        if e.get('career_goal') and (not isinstance(e['career_goal'],dict) or (e['career_goal'].get('target_role'),e['career_goal'].get('target_grade')) not in profiles):
            raise ValueError('Career goal must reference a catalog role and grade.')
        e.setdefault('work_format','hybrid')
        e.setdefault('preferred_language','en')
        e.setdefault('tenure_months',0)
        e.setdefault('career_goal',None)

def validate_history(rows, data):
    if not rows or len(rows)>20000:
        raise ValueError('Provide 1–20,000 history records.')
    employee_ids = {e['employee_id'] for e in data['employees']}
    event_ids = {e['event_id'] for e in data['events']}
    ids = set()
    completed_once = set()
    for r in rows:
        if any(not str(r.get(k,'')) for k in ('record_id','employee_id','event_id','date','status','completion_pct')):
            raise ValueError('Missing required history columns.')
        if r['record_id'] in ids or r['employee_id'] not in employee_ids or r['event_id'] not in event_ids:
            raise ValueError('History contains duplicate record IDs or unknown employee/event IDs. Import profiles first.')
        ids.add(r['record_id'])
        if r['status'] == 'completed' and r['event_id'] not in RECURRING:
            key = (r['employee_id'], r['event_id'])
            if key in completed_once:
                raise ValueError('A non-recurring event cannot have multiple completed records for the same employee.')
            completed_once.add(key)
        date.fromisoformat(r['date'])
        if r['date']>data['as_of_date']:
            raise ValueError('History date is after the dataset snapshot date.')
        if r['status'] not in MISSED|{'completed','in_progress'}:
            raise ValueError('Unknown participation status.')
        n=int(r['completion_pct'])
        if not 0<=n<=100 or (r['status']=='completed' and n!=100):
            raise ValueError('Completion percentage is invalid.')
