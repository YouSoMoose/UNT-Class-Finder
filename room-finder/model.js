export const DAY_CODES = 'UMTWRFS';
export const DAY_LABELS = {M:'Monday',T:'Tuesday',W:'Wednesday',R:'Thursday',F:'Friday',S:'Saturday',U:'Sunday'};
export function minutes(value) {
  const m = String(value || '').trim().match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
  if (!m || +m[2]>59 || (m[3] ? +m[1]<1 || +m[1]>12 : +m[1]>23)) return null;
  return (m[3] ? +m[1]%12 + (/pm/i.test(m[3])?12:0) : +m[1])*60 + +m[2];
}
export function dateKey(value) {
  const m=String(value||'').match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if(!m)return null;
  const d=new Date(+m[3],+m[1]-1,+m[2],12);
  return d.getFullYear()===+m[3]&&d.getMonth()===+m[1]-1&&d.getDate()===+m[2] ? `${m[3]}-${m[1].padStart(2,'0')}-${m[2].padStart(2,'0')}` : null;
}
export function dateRange(text) {
  const match=String(text||'').match(/(\d{1,2}\/\d{1,2}\/\d{4})\s*[-–]\s*(\d{1,2}\/\d{1,2}\/\d{4})/);
  if(!match)return null;
  const start=dateKey(match[1]),end=dateKey(match[2]);
  return start&&end&&start<=end ? {start,end} : null;
}
export function physicalRoom(value) {
  const room=String(value||'').replace(/\s+/g,' ').trim();
  if(!room||/\b(online|internet|remote|zoom|canvas|arranged|tba|tbd|web|virtual|none|unknown|no facility assigned)\b/i.test(room)||room.includes(','))return null;
  const parts=room.match(/\b[A-Za-z][A-Za-z0-9-]{1,15}\s+[A-Za-z]?\d[\w-]*\b/g);
  if(parts?.length>1)return new Set(parts.map(p=>p.toLowerCase())).size===1?parts[0]:null;
  return /^[A-Za-z][A-Za-z0-9 -]*\s+[A-Za-z]?\d[\w-]*$/.test(room)?room:null;
}
export function buildingOf(room) {
  return (String(room||'').match(/^(.*?)\s+[A-Z]?\d[\w-]*$/i)?.[1] || String(room||'')).toUpperCase();
}
function validDate(date) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(date||''))return false;
  const d=new Date(`${date}T12:00:00`);
  return Number.isFinite(+d)&&`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`===date;
}
export function indexRooms(data,titles={}) {
  const rooms=new Map(),seen=new Set();let excluded=0;
  for(const [code,course] of Object.entries(data.courses||{})) {
    for(const section of course.sections||[]) {
      // Enrollment options repeat lecture sections alongside different recitations.
      const rawPatterns=section.meetings?.length?section.meetings:section.linked_sections?.length?section.linked_sections:[section];
      const patterns=rawPatterns.flatMap(m=>{
        const raw=String(m.room??section.room??'');
        if(physicalRoom(raw))return [m];
        const candidates=[...new Set((raw.match(/\b[A-Za-z][A-Za-z0-9-]{1,15}\s+[A-Za-z]?\d[\w-]*\b/g)||[]).map(r=>r.toUpperCase()))];
        return candidates.length>1?candidates.map(room=>({...m,room,_ambiguous_room:true})):[m];
      });
      for(const m of patterns) {
        const room=physicalRoom(m.room??section.room);
        if(!room){excluded++;continue;}
        const roomId=room.toLowerCase(), start=minutes(m.start_time),end=minutes(m.end_time);
        const days=String(m.days||'').toUpperCase(),range=dateRange(m.meeting_dates||section.meeting_dates);
        const entry={code,title:titles[code]||course.course_name||'',room,section:m.section||section.section,classNumber:m.class_number||section.class_number,instructor:m.instructor||section.instructor||'',days,start,end,range,dates:m.meeting_dates||section.meeting_dates||'',rawTime:m.days_and_times||section.days_and_times||'Time to be arranged'};
        const flattenedTimes=(entry.rawTime.match(/\d{1,2}:\d{2}\s*[AP]M\s*(?:to|[-–])\s*\d{1,2}:\d{2}\s*[AP]M/gi)||[]).length>1;
        const ambiguousDates=new Set(entry.dates.match(/\d{1,2}\/\d{1,2}\/\d{4}\s*[-–]\s*\d{1,2}\/\d{1,2}\/\d{4}/g)||[]).size>1;
        entry.known=!m._ambiguous_room&&!flattenedTimes&&!ambiguousDates&&start!==null&&end!==null&&end>start&&!!days&&/^[UMTWRFS]+$/.test(days)&&!!range;
        const key=JSON.stringify([code,entry.classNumber,roomId,days,start,end,entry.dates]);
        if(seen.has(key))continue;seen.add(key);
        if(!rooms.has(roomId))rooms.set(roomId,{id:roomId,name:room,building:buildingOf(room),meetings:[]});
        rooms.get(roomId).meetings.push(entry);
      }
    }
  }
  return {rooms:[...rooms.values()].sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true})),excluded};
}
export function occursOn(m,date) {
  const d=new Date(`${date}T12:00:00`);
  return m.known&&validDate(date)&&date>=m.range.start&&date<=m.range.end&&m.days.includes(DAY_CODES[d.getDay()]);
}
export function occupancy(room,date,time,duration=0) {
  const at=minutes(time);
  if(at===null||!validDate(date)||duration<0||at+duration>1440)return {state:'unknown',active:[]};
  const active=room.meetings.filter(m=>occursOn(m,date)&&(duration?m.start<at+duration&&at<m.end:m.start<=at&&at<m.end));
  if(active.length)return {state:'occupied',active};
  const uncertain=room.meetings.some(m=>!m.known&&(!m.range||date>=m.range.start&&date<=m.range.end));
  return {state:uncertain?'unknown':'clear',active:[]};
}
export function nextFreeWindow(room,date,time,duration=30) {
  const at=minutes(time);
  if(at===null||!validDate(date)||duration<=0||room.meetings.some(m=>!m.known&&(!m.range||date>=m.range.start&&date<=m.range.end)))return null;
  let candidate=at;
  for(const m of room.meetings.filter(m=>occursOn(m,date)).sort((a,b)=>a.start-b.start)) {
    if(m.end<=candidate)continue;
    if(m.start>=candidate+duration)return candidate+duration<=1440?candidate:null;
    candidate=Math.max(candidate,m.end);
  }
  return candidate+duration<=1440?candidate:null;
}
export function matchesCourse(m,query) {
  const q=query.trim().toLowerCase().replace(/\s+/g,' ');
  return !q || `${m.code} ${m.title} ${m.instructor}`.toLowerCase().includes(q) || m.code.replace(/\s/g,'').toLowerCase().includes(q.replace(/\s/g,''));
}
export const campusOfRoom=room=>/^(FRLD|FRSC|FRISCO|CHEC)\b/i.test(room.name)?'frisco':new Set('ART ARTF AUDB BLB CHEM CHIL CURY ENV GAB GATE HKRY LANG LIFE MATT MHA MUSI NTDP PHYS SAGE SPHS WH WSC2'.split(' ')).has(room.building)?'denton':'unknown';
export function filterRooms(rooms,{query='',roomQuery='',building='all',campus='all',state='all',date,time,duration=0}) {
  return rooms.filter(room=>(campus==='all'||campusOfRoom(room)===campus)&&(building==='all'||room.building===building)&&room.name.toLowerCase().includes(roomQuery.trim().toLowerCase())&&room.meetings.some(m=>matchesCourse(m,query))&&(state==='all'||occupancy(room,date,time,duration).state===state));
}

export function dailyClasses(rooms,{date,time,query='',roomQuery='',building='all',campus='all',state='all',group='time'}){
 const at=minutes(time),items=[];
 for(const room of rooms){if((building!=='all'&&room.building!==building)||(campus!=='all'&&campusOfRoom(room)!==campus)||!room.name.toLowerCase().includes(roomQuery.trim().toLowerCase()))continue;
 for(const m of room.meetings){if(!occursOn(m,date)||!matchesCourse(m,query))continue;const phase=at!==null&&m.start<=at&&at<m.end?'active':at!==null&&m.start>at?'upcoming':'ended';if(state!=='all'&&phase!==state)continue;items.push({...m,roomId:room.id,building:room.building,phase});}}
 return items.sort((a,b)=>(group==='building'?a.building.localeCompare(b.building):group==='course'?a.code.localeCompare(b.code):0)||a.start-b.start||a.room.localeCompare(b.room)||String(a.classNumber).localeCompare(String(b.classNumber)));
}
