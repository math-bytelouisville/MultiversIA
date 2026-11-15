using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Collections.Generic;

public static class DemoHost {
    static void Reply(NetworkStream stream, int code, string type, byte[] body) {
        string label=code==200?"OK":code==404?"Not Found":code==405?"Method Not Allowed":"Bad Request";
        byte[] head=Encoding.ASCII.GetBytes("HTTP/1.1 "+code+" "+label+"\r\nContent-Type: "+type+"\r\nContent-Length: "+body.Length+"\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n\r\n");
        stream.Write(head,0,head.Length);stream.Write(body,0,body.Length);
    }
    static void Error(NetworkStream stream,int code) { Reply(stream,code,"application/json",Encoding.UTF8.GetBytes("{\"error\":\"invalid_request\"}")); }
    public static void Main(string[] args) {
        int port=int.Parse(args[0]);string root=Path.GetFullPath(args[1]);
        TcpListener listener=new TcpListener(IPAddress.Loopback,port);listener.Start();
        Console.WriteLine("READY "+((IPEndPoint)listener.LocalEndpoint).Port);
        while(true) {
            using(TcpClient client=listener.AcceptTcpClient()) {
                client.ReceiveTimeout=2000;client.SendTimeout=2000;
                using(NetworkStream stream=client.GetStream()) {
                    try {
                        StringBuilder buffer=new StringBuilder();bool complete=false;
                        for(int i=0;i<8192;i++) { int value=stream.ReadByte();if(value<0)break;if(value>127)break;buffer.Append((char)value);if(buffer.Length>=4 && buffer.ToString(buffer.Length-4,4)=="\r\n\r\n") {complete=true;break;} }
                        if(!complete){Error(stream,400);continue;}
                        string[] lines=buffer.ToString().Split(new string[]{"\r\n"},StringSplitOptions.None);
                        string[] request=lines[0].Split(' ');
                        if(request.Length!=3 || request[2]!="HTTP/1.1"){Error(stream,400);continue;}
                        if(request[0]!="GET"){Error(stream,405);continue;}
                        bool invalid=false;
                        for(int i=1;i<lines.Length && lines[i]!="";i++) {
                            int colon=lines[i].IndexOf(':');if(colon<=0){invalid=true;break;}
                            string key=lines[i].Substring(0,colon).Trim().ToLowerInvariant();string value=lines[i].Substring(colon+1).Trim();
                            if(key=="transfer-encoding" || (key=="content-length" && value!="0")){invalid=true;break;}
                        }
                        if(invalid){Error(stream,400);continue;}
                        string[] uri=request[1].Split(new char[]{'?'},2);string path=uri[0];
                        if(path=="/api/quote") {
                            Dictionary<string,string> query=new Dictionary<string,string>(StringComparer.Ordinal);
                            if(uri.Length!=2){Error(stream,400);continue;}
                            foreach(string pair in uri[1].Split('&')) { string[] kv=pair.Split('=');if(kv.Length!=2 || query.ContainsKey(kv[0])){invalid=true;break;}query[kv[0]]=kv[1]; }
                            string weightText,expressText;int weight;
                            if(invalid || query.Count!=2 || !query.TryGetValue("weight",out weightText) || !query.TryGetValue("express",out expressText)){Error(stream,400);continue;}
                            if(weightText.Length==0 || weightText.Length>4){Error(stream,400);continue;}
                            foreach(char c in weightText)if(c<'0' || c>'9')invalid=true;
                            if(invalid || !int.TryParse(weightText,out weight) || weight>5000 || (expressText!="0" && expressText!="1")){Error(stream,400);continue;}
                            bool express=expressText=="1";
                            int price=__ENTRY__(weight,express);
                            string json="{\"weight\":"+weight+",\"express\":"+(express?"true":"false")+",\"price_eur\":"+price+"}";
                            Reply(stream,200,"application/json",Encoding.UTF8.GetBytes(json));
                        } else if(path=="/api/meta") {
                            Reply(stream,200,"application/json",Encoding.UTF8.GetBytes("{\"language\":\"C#\"}"));
                        } else {
                            string file=path=="/"?"index.html":path=="/style.css"?"style.css":path=="/app.js"?"app.js":null;
                            if(file==null){Error(stream,404);continue;}
                            string type=file.EndsWith(".html")?"text/html; charset=utf-8":file.EndsWith(".css")?"text/css; charset=utf-8":"text/javascript; charset=utf-8";
                            Reply(stream,200,type,File.ReadAllBytes(Path.Combine(root,file)));
                        }
                    } catch { try{Error(stream,400);}catch{} }
                }
            }
        }
    }
}
