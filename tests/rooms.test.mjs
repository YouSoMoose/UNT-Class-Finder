import test from 'node:test';
import assert from 'node:assert/strict';
import {indexRooms,occupancy,filterRooms,dateRange,nextFreeWindow,buildingOf,dailyClasses} from '../room-finder/model.js';
const lecture={room:'Wh 316',section:'001',class_number:'1001',days:'MWF',start_time:'9:00AM',end_time:'10:00AM',meeting_dates:'08/24/2026 - 12/11/2026'};
const recitation={...lecture,room:'Wh 110',section:'002',class_number:'1002',days:'T',start_time:'1:00PM',end_time:'2:00PM'};
const fixture={courses:{'MATH 1680':{sections:[{meetings:[lecture,recitation]},{meetings:[lecture,{...recitation,class_number:'1003',room:'Wh 111'}]}]},'ENGL 1310':{sections:[{meetings:[{...lecture,class_number:'2001',days:'T',start_time:'9:00AM',end_time:'10:00AM'}]}]}}};
test('paired enrollment options preserve recitations and deduplicate shared lectures',()=>{const {rooms}=indexRooms(fixture);assert.equal(rooms.length,3);assert.equal(rooms.find(r=>r.name==='Wh 316').meetings.length,2);});
test('occupancy respects weekdays, term dates and exclusive end times',()=>{const r=indexRooms(fixture).rooms.find(r=>r.name==='Wh 316');assert.equal(occupancy(r,'2026-09-30','09:30').state,'occupied');assert.equal(occupancy(r,'2026-09-30','10:00').state,'clear');assert.equal(occupancy(r,'2026-09-27','09:30').state,'clear');assert.equal(occupancy(r,'2027-09-30','09:30').state,'clear');});
test('MATH course filter still uses ENGL occupancy in the same room',()=>{const rooms=indexRooms(fixture,{'MATH 1680':'Statistics'}).rooms;const filtered=filterRooms(rooms,{query:'MATH',state:'occupied',date:'2026-09-29',time:'09:30'});assert.equal(filtered.length,1);assert.equal(occupancy(filtered[0],'2026-09-29','09:30').active[0].code,'ENGL 1310');assert.equal(filterRooms(rooms,{query:'statistics',date:'2026-09-30',time:'12:00'}).length,3);assert.equal(filterRooms(rooms,{query:'math1680',date:'2026-09-30',time:'12:00'}).length,3);});
test('unspecified schedules produce unknown status and virtual rooms are excluded',()=>{const d={courses:{A:{sections:[{...lecture,start_time:'TBA'},{...lecture,room:'Internet'}]}}};const {rooms,excluded}=indexRooms(d);assert.equal(excluded,1);assert.equal(occupancy(rooms[0],'2026-09-30','09:00').state,'unknown');});
test('short session bounds and invalid calendar dates',()=>{const r=indexRooms({courses:{A:{sections:[{...lecture,meeting_dates:'10/12/2026 - 12/11/2026'}]}}}).rooms[0];assert.equal(occupancy(r,'2026-09-30','09:30').state,'clear');assert.equal(occupancy(r,'2026-10-14','09:30').state,'occupied');assert.equal(dateRange('02/30/2026 - 12/11/2026'),null);});
test('free-window queries consider the entire interval and merge overlapping classes',()=>{
  const rooms=indexRooms({courses:{A:{sections:[lecture,{...lecture,class_number:'1005',start_time:'9:30AM',end_time:'10:30AM'}]}}}).rooms;
  assert.equal(occupancy(rooms[0],'2026-09-30','08:45',30).state,'occupied');
  assert.equal(nextFreeWindow(rooms[0],'2026-09-30','09:00',60),630);
  assert.equal(occupancy(rooms[0],'2026-09-30','10:30',60).state,'clear');
  assert.equal(occupancy(rooms[0],'2026-02-30','10:30').state,'unknown');
  assert.equal(occupancy(rooms[0],'2026-09-30','23:30',60).state,'unknown');
});
test('building filter and uncertain schedules never promise a free window',()=>{
  const rooms=indexRooms(fixture).rooms;
  assert.equal(buildingOf('LIFE A419'),'LIFE');
  assert.equal(filterRooms(rooms,{building:'WH',date:'2026-09-30',time:'09:00'}).length,3);
  assert.equal(filterRooms(rooms,{building:'BLB',date:'2026-09-30',time:'09:00'}).length,0);
  const uncertain=indexRooms({courses:{A:{sections:[{...lecture,start_time:'TBA'}]}}}).rooms[0];
  assert.equal(nextFreeWindow(uncertain,'2026-09-30','09:00',30),null);
  assert.equal(indexRooms({courses:{A:{sections:[{...lecture,room:'None'}]}}}).rooms.length,0);
  const ambiguous=indexRooms({courses:{A:{sections:[{...lecture,room:'Smith,Ann'},{...lecture,room:'SAGE 356 WH 317'}]}}}).rooms;
  assert.equal(ambiguous.length,2);
  assert.ok(ambiguous.every(r=>occupancy(r,'2026-09-30','11:00').state==='unknown'));
});

test('campus filters keep unknown buildings out of Denton and preserve occupancy',()=>{const rooms=indexRooms({courses:{'MATH 1680':{sections:[lecture,{...lecture,room:'FRLD 160',class_number:'3'},{...lecture,room:'MYSTERY 101',class_number:'4'}]}}}).rooms;assert.deepEqual(filterRooms(rooms,{campus:'frisco'}).map(r=>r.name),['FRLD 160']);assert.deepEqual(filterRooms(rooms,{campus:'denton'}).map(r=>r.name),['Wh 316']);assert.equal(filterRooms(rooms,{campus:'all'}).length,3);});

test('daily class organization follows dates, campus and current meeting boundaries',()=>{const rs=indexRooms(fixture).rooms;const active=dailyClasses(rs,{date:'2026-09-29',time:'09:30',state:'active'});assert.equal(active.length,1);assert.equal(active[0].code,'ENGL 1310');assert.equal(dailyClasses(rs,{date:'2026-09-29',time:'10:00',state:'active'}).length,0);assert.equal(dailyClasses(rs,{date:'2026-09-29',time:'10:00',state:'upcoming',query:'MATH'}).length,2);assert.equal(dailyClasses(rs,{date:'2026-09-29',time:'09:30',campus:'frisco'}).length,0);assert.equal(dailyClasses(rs,{date:'2026-09-27',time:'09:30'}).length,0);});


test('a day without a selected time never labels every class finished',()=>{const list=dailyClasses(indexRooms(fixture).rooms,{date:'2026-09-29',time:''});assert.ok(list.length);assert.ok(list.every(m=>m.phase==='scheduled'));});
