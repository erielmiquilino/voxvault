//! A small HTTP/1.1 client for the loopback interface, written on
//! `std::net::TcpStream`.
//!
//! Pulling a general HTTP stack in for this would be the wrong trade. The only
//! endpoint it ever speaks to is the resident service on 127.0.0.1: no TLS, no
//! proxies, no redirects, no name resolution. A general client would add
//! megabytes of binary and a runtime to a process whose whole reason to exist
//! is a 700 MB ceiling across the tree.
//!
//! What it does support is what that conversation needs: a request timeout, the
//! session secret header, `Content-Length` and chunked responses.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

pub const SECRET_HEADER: &str = "X-VoxVault-Token";

#[derive(Debug)]
pub enum HttpError {
    Connect(String),
    Io(String),
    /// The service accepted the request and did not answer within the
    /// budget. Not the same as a failure: it may still be doing what it was
    /// asked, and the caller has to say so rather than report it as refused.
    Prazo(String),
    Protocol(String),
    Status { code: u16, body: String },
}

fn io(err: std::io::Error) -> HttpError {
    match err.kind() {
        std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock => {
            HttpError::Prazo(err.to_string())
        }
        _ => HttpError::Io(err.to_string()),
    }
}

impl HttpError {
    pub fn mensagem(&self) -> String {
        match self {
            HttpError::Connect(detail) => {
                format!("Não foi possível alcançar o serviço local: {detail}")
            }
            HttpError::Io(detail) => format!("Falha de comunicação com o serviço: {detail}"),
            HttpError::Prazo(detail) => {
                format!("O serviço local não respondeu dentro do prazo: {detail}")
            }
            HttpError::Protocol(detail) => format!("Resposta inesperada do serviço: {detail}"),
            HttpError::Status { code, body } => {
                if *code == 401 || *code == 403 {
                    "O serviço local recusou o segredo desta sessão. \
                     Ele foi reiniciado; reconecte."
                        .to_string()
                } else {
                    format!("O serviço local respondeu {code}: {body}")
                }
            }
        }
    }
}

pub struct Response {
    pub status: u16,
    pub body: String,
}

/// One request/response exchange. The connection is not kept alive: these calls
/// are rare by design, and a pooled socket would be another thing to supervise.
pub fn request(
    addr: SocketAddr,
    method: &str,
    path: &str,
    secret: &str,
    body: Option<&str>,
    timeout: Duration,
) -> Result<Response, HttpError> {
    let stream = TcpStream::connect_timeout(&addr, timeout)
        .map_err(|err| HttpError::Connect(err.to_string()))?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|_| stream.set_write_timeout(Some(timeout)))
        .and_then(|_| stream.set_nodelay(true))
        .map_err(io)?;

    let payload = body.unwrap_or("");
    let mut head = format!(
        "{method} {path} HTTP/1.1\r\n\
         Host: 127.0.0.1\r\n\
         {SECRET_HEADER}: {secret}\r\n\
         Accept: application/json\r\n\
         Connection: close\r\n"
    );
    if body.is_some() {
        head.push_str("Content-Type: application/json; charset=utf-8\r\n");
    }
    head.push_str(&format!("Content-Length: {}\r\n\r\n", payload.len()));

    let mut stream = stream;
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(payload.as_bytes()))
        .and_then(|_| stream.flush())
        .map_err(io)?;

    read_response(stream)
}

fn read_response(stream: TcpStream) -> Result<Response, HttpError> {
    let mut reader = BufReader::new(stream);

    let mut status_line = String::new();
    reader
        .read_line(&mut status_line)
        .map_err(io)?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| HttpError::Protocol(format!("linha de status: {status_line:?}")))?;

    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    loop {
        let mut line = String::new();
        let read = reader
            .read_line(&mut line)
            .map_err(io)?;
        if read == 0 || line.trim().is_empty() {
            break;
        }
        let (name, value) = match line.split_once(':') {
            Some((name, value)) => (name.trim().to_ascii_lowercase(), value.trim().to_string()),
            None => continue,
        };
        match name.as_str() {
            "content-length" => content_length = value.parse().ok(),
            "transfer-encoding" if value.eq_ignore_ascii_case("chunked") => chunked = true,
            _ => {}
        }
    }

    let mut raw = Vec::new();
    if chunked {
        read_chunked(&mut reader, &mut raw)?;
    } else if let Some(length) = content_length {
        raw.resize(length, 0);
        reader
            .read_exact(&mut raw)
            .map_err(io)?;
    } else {
        reader
            .read_to_end(&mut raw)
            .map_err(io)?;
    }

    let body = String::from_utf8_lossy(&raw).into_owned();
    if !(200..300).contains(&status) {
        return Err(HttpError::Status { code: status, body });
    }
    Ok(Response { status, body })
}

fn read_chunked(reader: &mut BufReader<TcpStream>, out: &mut Vec<u8>) -> Result<(), HttpError> {
    loop {
        let mut size_line = String::new();
        reader
            .read_line(&mut size_line)
            .map_err(io)?;
        let size = usize::from_str_radix(size_line.trim().split(';').next().unwrap_or("0"), 16)
            .map_err(|_| HttpError::Protocol(format!("tamanho de bloco: {size_line:?}")))?;
        if size == 0 {
            break;
        }
        let start = out.len();
        out.resize(start + size, 0);
        reader
            .read_exact(&mut out[start..])
            .map_err(io)?;
        let mut terminator = [0u8; 2];
        reader
            .read_exact(&mut terminator)
            .map_err(io)?;
    }
    Ok(())
}
