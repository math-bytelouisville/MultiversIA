// Explicit local qcm-mvc-1 adapter. Business rules are generated separately.
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::{Duration, Instant};
mod rules;

const EXPECTED: [i32; 3] = [__EXPECTED__];
const HTML: &[u8] = include_bytes!("index.html");
const CSS: &[u8] = include_bytes!("style.css");
const JS: &[u8] = include_bytes!("app.js");
const QUESTIONS: &[u8] = include_bytes!("public-questions.json");

fn reply(stream: &mut TcpStream, code: u16, typ: &str, body: &[u8], api: bool) {
    let label = match code {200 => "OK", 404 => "Not Found", 405 => "Method Not Allowed", _ => "Bad Request"};
    let cache = if api {"Cache-Control: no-store\r\n"} else {""};
    let allow = if code == 405 {"Allow: GET\r\n"} else {""};
    let head = format!("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n{}{}\r\n", code, label, typ, body.len(), cache, allow);
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body);
}

fn error(stream: &mut TcpStream, code: u16, api: bool) {
    let body: &[u8] = match code {
        404 => b"{\"error\":\"NOT_FOUND\"}",
        405 => b"{\"error\":\"METHOD_NOT_ALLOWED\"}",
        _ => b"{\"error\":\"INVALID_ANSWERS\"}",
    };
    reply(stream, code, "application/json; charset=utf-8", body, api);
}

fn answers(query: Option<&str>) -> Option<[i32; 3]> {
    let mut values = [None; 3];
    for pair in query?.split('&') {
        let (key, value) = pair.split_once('=')?;
        let index = match key {"a" => 0, "b" => 1, "c" => 2, _ => return None};
        let bytes = value.as_bytes();
        if values[index].is_some() || bytes.len() != 1 || !(b'0'..=b'3').contains(&bytes[0]) { return None; }
        values[index] = Some((bytes[0] - b'0') as i32);
    }
    Some([values[0]?, values[1]?, values[2]?])
}

fn handle(stream: &mut TcpStream) {
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut raw = Vec::new();
    let mut one = [0u8; 1];
    let mut complete = false;
    for _ in 0..8192 {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() { break; }
        let _ = stream.set_read_timeout(Some(remaining));
        match stream.read(&mut one) { Ok(1) if one[0] < 128 => raw.push(one[0]), _ => break }
        if raw.ends_with(b"\r\n\r\n") { complete = true; break; }
    }
    if !complete { error(stream, 400, true); return; }
    let text = String::from_utf8(raw).unwrap();
    let mut lines = text.split("\r\n");
    let request: Vec<_> = lines.next().unwrap_or("").split(' ').collect();
    if request.len() != 3 || !matches!(request[2], "HTTP/1.1" | "HTTP/1.0") || !request[1].starts_with('/') {
        error(stream, 400, true); return;
    }
    let mut content_length = false;
    for line in lines {
        if line.is_empty() { break; }
        let Some((key, value)) = line.split_once(':') else { error(stream, 400, true); return; };
        if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
            error(stream, 400, true); return;
        }
        let key = key.to_ascii_lowercase();
        if key == "transfer-encoding" || (key == "content-length" && (content_length || value.trim() != "0")) {
            error(stream, 400, true); return;
        }
        if key == "content-length" { content_length = true; }
    }
    let (path, query) = match request[1].split_once('?') { Some((p, q)) => (p, Some(q)), None => (request[1], None) };
    let api = path.starts_with("/api/");
    if !matches!(path, "/" | "/style.css" | "/app.js" | "/api/meta" | "/api/quiz/questions" | "/api/quiz/grade") {
        error(stream, 404, api); return;
    }
    if request[0] != "GET" { error(stream, 405, api); return; }
    if path != "/api/quiz/grade" && query.is_some() { error(stream, 400, api); return; }
    match path {
        "/" => reply(stream, 200, "text/html; charset=utf-8", HTML, false),
        "/style.css" => reply(stream, 200, "text/css; charset=utf-8", CSS, false),
        "/app.js" => reply(stream, 200, "text/javascript; charset=utf-8", JS, false),
        "/api/meta" => reply(stream, 200, "application/json; charset=utf-8", b"{\"language\":\"Rust\",\"framework\":\"Rust std\",\"profile\":\"qcm-mvc-1\"}", true),
        "/api/quiz/questions" => reply(stream, 200, "application/json; charset=utf-8", QUESTIONS, true),
        "/api/quiz/grade" => {
            let Some(a) = answers(query) else { error(stream, 400, true); return; };
            let correct = [rules::__IS_CORRECT__(a[0], EXPECTED[0]), rules::__IS_CORRECT__(a[1], EXPECTED[1]), rules::__IS_CORRECT__(a[2], EXPECTED[2])];
            let score = rules::__SCORE__(correct[0], correct[1], correct[2]);
            let passed = rules::__PASSED__(score);
            let body = format!("{{\"score\":{},\"total\":3,\"passed\":{},\"correct\":[{},{},{}],\"expected\":[{},{},{}]}}", score, passed, correct[0], correct[1], correct[2], EXPECTED[0], EXPECTED[1], EXPECTED[2]);
            reply(stream, 200, "application/json; charset=utf-8", body.as_bytes(), true);
        }
        _ => unreachable!(),
    }
}

fn main() -> std::io::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    let port = args.get(1).and_then(|x| x.parse::<u16>().ok()).ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidInput, "one valid port argument required"))?;
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    println!("READY {}", listener.local_addr()?.port());
    std::io::stdout().flush()?;
    for incoming in listener.incoming() {
        if let Ok(mut stream) = incoming { handle(&mut stream); }
    }
    Ok(())
}
