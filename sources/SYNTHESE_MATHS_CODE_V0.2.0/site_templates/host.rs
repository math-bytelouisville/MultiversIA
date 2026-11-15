use std::io::{Read,Write};
use std::net::{TcpListener,TcpStream};
use std::time::Duration;
use std::collections::BTreeMap;
use std::path::Path;
mod rules;

fn reply(s:&mut TcpStream,code:u16,typ:&str,body:&[u8]) {
    let label=match code {200=>"OK",404=>"Not Found",405=>"Method Not Allowed",_=>"Bad Request"};
    let head=format!("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n\r\n",code,label,typ,body.len());
    let _=s.write_all(head.as_bytes());let _=s.write_all(body);
}
fn error(s:&mut TcpStream,code:u16){reply(s,code,"application/json",b"{\"error\":\"invalid_request\"}");}
fn handle(s:&mut TcpStream,root:&Path) {
    let _=s.set_read_timeout(Some(Duration::from_secs(2)));let _=s.set_write_timeout(Some(Duration::from_secs(2)));
    let mut raw=Vec::new();let mut one=[0u8;1];let mut complete=false;
    for _ in 0..8192 { match s.read(&mut one) {Ok(1) if one[0]<128=>raw.push(one[0]),_=>break};if raw.ends_with(b"\r\n\r\n"){complete=true;break;} }
    if !complete {error(s,400);return;}
    let text=String::from_utf8(raw).unwrap();let mut lines=text.split("\r\n");let first=lines.next().unwrap_or("");let request:Vec<_>=first.split(' ').collect();
    if request.len()!=3 || request[2]!="HTTP/1.1"{error(s,400);return;}
    if request[0]!="GET"{error(s,405);return;}
    for line in lines {if line.is_empty(){break;}let Some((key,value))=line.split_once(':') else {error(s,400);return;};
        let key=key.trim().to_ascii_lowercase();let value=value.trim();
        if key.is_empty() || key=="transfer-encoding" || (key=="content-length" && value!="0"){error(s,400);return;}
    }
    let (path,query)=request[1].split_once('?').unwrap_or((request[1],""));
    if path=="/api/quote" {
        let mut values=BTreeMap::new();
        for pair in query.split('&'){let kv:Vec<_>=pair.split('=').collect();if kv.len()!=2 || values.insert(kv[0],kv[1]).is_some(){error(s,400);return;}}
        let (Some(w),Some(e))=(values.get("weight"),values.get("express")) else {error(s,400);return;};
        if values.len()!=2 || w.is_empty() || w.len()>4 || !w.bytes().all(|c|c.is_ascii_digit()) || (*e!="0" && *e!="1"){error(s,400);return;}
        let Ok(weight)=w.parse::<i32>() else {error(s,400);return;};if weight>5000{error(s,400);return;}let express=*e=="1";
        let price=rules::__ENTRY__(weight,express);
        let json=format!("{{\"weight\":{},\"express\":{},\"price_eur\":{}}}",weight,express,price);reply(s,200,"application/json",json.as_bytes());
    } else if path=="/api/meta" {reply(s,200,"application/json",b"{\"language\":\"Rust\"}");}
    else {let (file,typ)=match path {"/"=>("index.html","text/html; charset=utf-8"),"/style.css"=>("style.css","text/css; charset=utf-8"),"/app.js"=>("app.js","text/javascript; charset=utf-8"),_=>{error(s,404);return;}};
        match std::fs::read(root.join(file)){Ok(body)=>reply(s,200,typ,&body),Err(_)=>error(s,400)}
    }
}
fn main(){let args:Vec<_>=std::env::args().collect();let port=args[1].parse::<u16>().unwrap();let root=Path::new(&args[2]);let listener=TcpListener::bind(("127.0.0.1",port)).unwrap();println!("READY {}",listener.local_addr().unwrap().port());let _=std::io::stdout().flush();for incoming in listener.incoming(){if let Ok(mut stream)=incoming{handle(&mut stream,root);}}}
