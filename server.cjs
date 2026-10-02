const http=require('http'),fs=require('fs'),path=require('path');
const allowed=new Set(['/room-finder/index.html','/room-finder/app.js','/room-finder/model.js','/room-finder/style.css','/public/data/fall-2026.json','/public/data/catalog-index.json']);
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json'};
const server=http.createServer((req,res)=>{
  let name;try{name=new URL(req.url,'http://localhost').pathname;}catch{res.writeHead(400);res.end();return;}
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);res.end();return;}
  if(name==='/'||name==='/room-finder/'||name==='/room-finder')name='/room-finder/index.html';
  if(!allowed.has(name)){res.writeHead(404);res.end();return;}
  fs.readFile(path.join(__dirname,name),(err,body)=>{
    if(err){res.writeHead(404);res.end();return;}
    res.writeHead(200,{'Content-Type':types[path.extname(name)],'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:body);
  });
});
server.on('error',error=>{console.error('Room Finder could not start:',error.message);process.exitCode=1;});
const port=Number(process.env.ROOM_FINDER_PORT||5175);
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('Invalid ROOM_FINDER_PORT');
server.listen(port,'127.0.0.1',()=>console.log(`Room Finder: http://127.0.0.1:${port}/room-finder/ — Ctrl+C to stop`));

