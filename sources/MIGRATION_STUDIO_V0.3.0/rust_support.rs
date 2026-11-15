// Closed, bounded numeric protocol and exact rational primitives.
// Point/aggregate formulas are generated separately from the validated AST.
use std::io::{self, Read};
use std::cmp::Ordering;
type R<T> = Result<T, &'static str>;
#[derive(Clone,Copy)]
struct Rat { n:i128, d:i128 }
fn ck(v:Option<i128>)->R<i128>{let x=v.ok_or("i128 overflow")?;if x==i128::MIN{Err("i128 overflow")}else{Ok(x)}}
fn gcd(mut a:i128,mut b:i128)->i128 {while b!=0 {let r=a%b;a=b;b=r;}a}
impl Rat {
 fn new(n:i128,d:i128)->R<Self>{if d==0{return Err("division by zero")}if n==i128::MIN||d==i128::MIN{return Err("i128 overflow")}let (n,d)=if d<0{(-n,-d)}else{(n,d)};let g=gcd(n.abs(),d);Ok(Self{n:n/g,d:d/g})}
 fn int(n:i128)->R<Self>{Self::new(n,1)}
 fn add(self,b:Self)->R<Self>{let n=ck(ck(self.n.checked_mul(b.d))?.checked_add(ck(b.n.checked_mul(self.d))?))?;Self::new(n,ck(self.d.checked_mul(b.d))?)}
 fn sub(self,b:Self)->R<Self>{let n=ck(ck(self.n.checked_mul(b.d))?.checked_sub(ck(b.n.checked_mul(self.d))?))?;Self::new(n,ck(self.d.checked_mul(b.d))?)}
 fn mul(self,b:Self)->R<Self>{Self::new(ck(self.n.checked_mul(b.n))?,ck(self.d.checked_mul(b.d))?)}
 fn div(self,b:Self)->R<Self>{Self::new(ck(self.n.checked_mul(b.d))?,ck(self.d.checked_mul(b.n))?)}
 fn cmp(self,b:Self)->R<Ordering>{Ok(ck(self.n.checked_mul(b.d))?.cmp(&ck(b.n.checked_mul(self.d))?))}
 fn record(self)->String{format!("{{\"n\":\"{}\",\"d\":\"{}\"}}",self.n,self.d)}
 fn display(self)->R<String>{if self.n<0{return Err("negative display")}let hundred=ck(self.n.checked_mul(100))?;let mut cents=hundred/self.d;let rem=hundred%self.d;if ck(rem.checked_mul(2))?>=self.d{cents=ck(cents.checked_add(1))?}Ok(format!("{}.{:02}",cents/100,cents%100))}
}
fn token<'a>(parts:&mut impl Iterator<Item=&'a str>)->R<i128>{let text=parts.next().ok_or("missing token")?;if text.len()>40||text.is_empty()||!text.bytes().all(|c|c.is_ascii_digit()){return Err("integer token refused")}let value=text.parse::<i128>().map_err(|_|"integer token overflow")?;if value<0{return Err("negative token")}Ok(value)}
fn bit(n:i128)->R<bool>{if n>1{Err("boolean token refused")}else{Ok(n==1)}}
fn decimal_threshold(t:Rat)->R<()> {
 if t.n<0||t.cmp(Rat::int(100)?)?==Ordering::Greater{return Err("threshold range")}
 let(mut d,mut twos,mut fives)=(t.d,0,0);while d%2==0{d/=2;twos+=1}while d%5==0{d/=5;fives+=1}
 let scale=twos.max(fives);if d!=1||scale>28{return Err("threshold decimal domain")}
 let mut coefficient=t.n;for _ in twos..scale{coefficient=ck(coefficient.checked_mul(2))?}for _ in fives..scale{coefficient=ck(coefficient.checked_mul(5))?}
 if coefficient>79228162514264337593543950335i128{return Err("threshold decimal coefficient")}Ok(())
}
fn run_case(line:&str)->R<String>{
 if line.len()>16384||!line.is_ascii(){return Err("line bound or encoding")}
 let mut parts=line.split_ascii_whitespace();let count=token(&mut parts)?;if !(1..=50).contains(&count){return Err("question count")}
 let partial=bit(token(&mut parts)?)?;let tn=token(&mut parts)?;let td=token(&mut parts)?;let threshold=Rat::new(tn,td)?;decimal_threshold(threshold)?;
 let mut sum=Rat::int(0)?;let mut correct=0;let mut points=Vec::new();
 for _ in 0..count {
  let g=token(&mut parts)?;let b=token(&mut parts)?;let sg=token(&mut parts)?;let sb=token(&mut parts)?;let exact=bit(token(&mut parts)?)?;let single=bit(token(&mut parts)?)?;
  if g<1||g>8||b>7||!(2..=8).contains(&(g+b))||sg>g||sb>b||sg+sb<1||exact!=(sg==g&&sb==0)||single&&(g!=1||sg+sb!=1){return Err("question count invariants")}
  let value=point(Rat::int(g)?,Rat::int(b)?,Rat::int(sg)?,Rat::int(sb)?,exact,single,partial)?;
  if value.n<0||value.cmp(Rat::int(1)?)?==Ordering::Greater{return Err("point result range")}
  sum=sum.add(value)?;if exact{correct+=1}points.push(format!("{{\"n\":\"{}\",\"d\":\"{}\",\"display\":\"{}\",\"is_correct\":{}}}",value.n,value.d,value.display()?,exact));
 }
 if parts.next().is_some(){return Err("extra token")}
 let count_rat=Rat::int(count)?;let score=aggregate_score(sum,count_rat)?;
 if score.n<0||score.cmp(count_rat)?==Ordering::Greater{return Err("score result range")}
 let percent=aggregate_percent(score,count_rat)?;if percent.n<0||percent.cmp(Rat::int(100)?)?==Ordering::Greater{return Err("percent result range")}
 let passed=aggregate_passed(percent,threshold)?;
 Ok(format!("{{\"score\":{},\"percent\":{},\"score_display\":\"{}\",\"percent_display\":\"{}\",\"passed\":{},\"correct_count\":{},\"points\":[{}]}}",score.record(),percent.record(),score.display()?,percent.display()?,passed,correct,points.join(",")))
}
fn failure(message:&str){println!("{{\"error\":\"REFUSED\",\"message\":\"{}\"}}",message);}
fn main(){
 let mut input=String::new();if io::stdin().take(8388609).read_to_string(&mut input).is_err(){failure("input encoding");return}
 if input.len()>8388608||input.is_empty()||input.lines().count()>4096{failure("input batch limit");return}
 for line in input.lines(){match run_case(line){Ok(result)=>println!("{}",result),Err(message)=>failure(message)}}
}
