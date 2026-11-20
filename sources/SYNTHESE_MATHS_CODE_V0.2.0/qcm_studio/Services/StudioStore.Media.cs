using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using QcmStudio.Models;

namespace QcmStudio.Services;

public sealed partial class StudioStore {
    public const int MaximumMediaBytes = 512 * 1024;
    public const long MaximumMediaStorageBytes = 32L * 1024 * 1024;
    public const int MaximumMediaFiles = 256;
    private HashSet<string>? validatedMedia;
    private bool requireMediaFiles = true;
    private void MediaValidationScope(Action action, bool requireFiles = true) {
        if (validatedMedia != null) { action(); return; }
        validatedMedia = new HashSet<string>(StringComparer.Ordinal);
        requireMediaFiles = requireFiles;
        try { action(); } finally { validatedMedia = null; requireMediaFiles = true; }
    }
    private string MediaDirectory => Path.Combine(Path.GetDirectoryName(statePath)!, "media");
    private static bool MediaId(string? id) => id != null && Regex.IsMatch(id, "\\A[a-f0-9]{64}\\z", RegexOptions.CultureInvariant);
    private static void Regular(string path, bool directory) {
        var attributes = File.GetAttributes(path);
        Need((attributes & FileAttributes.ReparsePoint) == 0 && ((attributes & FileAttributes.Directory) != 0) == directory, "Média lié ou type de fichier interdit.");
    }
    private void ValidateImage(QuizImage? image) {
        if (image == null) return;
        Need(MediaId(image.Id) && Text(image.Alt, 300) && image.Position is "above" or "below" or "left" or "right"
             && image.Size is "small" or "medium" or "large" && image.Fit is "contain" or "cover", "Référence ou présentation d’image invalide.");
        if (!requireMediaFiles || validatedMedia?.Contains(image.Id) == true) return;
        ReadMedia(image.Id); validatedMedia?.Add(image.Id);
    }
    public (byte[] Bytes, string ContentType) Media(string id) { lock (gate) return ReadMedia(id); }
    private (byte[] Bytes, string ContentType) ReadMedia(string id) {
        Need(MediaId(id), "Identifiant de média invalide.");
        if (!Directory.Exists(MediaDirectory)) throw new StudioException(404, "MEDIA_NOT_FOUND", "Image locale introuvable.");
        Regular(MediaDirectory, true); var path = Path.Combine(MediaDirectory, id);
        if (!File.Exists(path)) throw new StudioException(404, "MEDIA_NOT_FOUND", "Image locale introuvable.");
        Regular(path, false); Need(new FileInfo(path).Length is > 0 and <= MaximumMediaBytes, "Taille de média invalide.");
        var bytes = File.ReadAllBytes(path); Need(bytes.Length <= MaximumMediaBytes && Convert.ToHexStringLower(SHA256.HashData(bytes)) == id, "Image locale modifiée ou corrompue.");
        return (bytes, RasterType(bytes));
    }
    public object UploadMedia(MediaInput input) {
        lock (gate) {
            Need(input?.DataUrl != null && input.DataUrl.Length <= 32 + ((MaximumMediaBytes + 2) / 3 * 4), "Image requise, limitée à 512 Kio.");
            var value = input!.DataUrl; var comma = value.IndexOf(',');
            Need(comma > 0, "Image encodée invalide.");
            var declared = value[..comma] switch { "data:image/png;base64" => "image/png", "data:image/jpeg;base64" => "image/jpeg", "data:image/webp;base64" => "image/webp", _ => "" };
            Need(declared.Length > 0, "Choisis une image PNG, JPEG ou WebP locale.");
            byte[] bytes;
            try { bytes = Convert.FromBase64String(value[(comma + 1)..]); } catch (FormatException) { throw new StudioException(400, "INVALID_INPUT", "Encodage de l’image invalide."); }
            Need(bytes.Length is > 0 and <= MaximumMediaBytes, "L’image dépasse 512 Kio.");
            Need(RasterType(bytes) == declared, "Le contenu de l’image ne correspond pas à son format.");
            var id = Convert.ToHexStringLower(SHA256.HashData(bytes));
            if (Directory.Exists(MediaDirectory)) Regular(MediaDirectory, true);
            else Directory.CreateDirectory(MediaDirectory);
            var target = Path.Combine(MediaDirectory, id);
            if (File.Exists(target)) { ReadMedia(id); return new { id, url = "/api/media/" + id }; }
            long total = 0; int count = 0;
            foreach (var file in Directory.EnumerateFileSystemEntries(MediaDirectory)) { Regular(file, false); total = checked(total + new FileInfo(file).Length); count++; }
            Conflict(count >= MaximumMediaFiles || total + bytes.Length > MaximumMediaStorageBytes, "MEDIA_STORAGE_LIMIT", "Stockage des images plein (256 fichiers ou 32 Mio). Aucune ancienne image n’a été effacée.");
            var temporary = Path.Combine(MediaDirectory, id + "." + Guid.NewGuid().ToString("N") + ".pending");
            try {
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { file.Write(bytes); file.Flush(true); }
                File.Move(temporary, target, false);
            } finally { if (File.Exists(temporary)) File.Delete(temporary); }
            return new { id, url = "/api/media/" + id };
        }
    }
    private static void Dimensions(int width, int height) => Need(width is > 0 and <= 8192 && height is > 0 and <= 8192 && (long)width * height <= 16_000_000, "Dimensions d’image invalides (8 192 px par côté, 16 millions de pixels maximum).");
    // Bounded container checks, not a complete JPEG/WebP pixel decoder. SVG and external URLs are never accepted.
    private static string RasterType(byte[] bytes) {
        try {
            if (bytes.Length >= 8 && bytes.AsSpan(0, 8).SequenceEqual(new byte[] {137,80,78,71,13,10,26,10})) { CheckPng(bytes); return "image/png"; }
            if (bytes.Length >= 4 && bytes[0] == 255 && bytes[1] == 216) { CheckJpeg(bytes); return "image/jpeg"; }
            if (bytes.Length >= 12 && bytes.AsSpan(0, 4).SequenceEqual("RIFF"u8) && bytes.AsSpan(8, 4).SequenceEqual("WEBP"u8)) { CheckWebp(bytes); return "image/webp"; }
        } catch (Exception error) when (error is InvalidDataException or OverflowException or ArgumentException or IndexOutOfRangeException) { throw new StudioException(400, "INVALID_INPUT", "Structure d’image invalide."); }
        throw new StudioException(400, "INVALID_INPUT", "Format d’image non reconnu.");
    }
    private static uint Crc(ReadOnlySpan<byte> data) { uint value = uint.MaxValue; foreach (var b in data) { value ^= b; for (var i=0;i<8;i++) value=(value&1)!=0?(value>>1)^0xedb88320u:value>>1; } return ~value; }
    private static void CheckPng(byte[] b) {
        int offset=8, width=0,height=0,channels=0; bool header=false,end=false,palette=false; using var packed=new MemoryStream();
        while(offset < b.Length) {
            Need(b.Length-offset>=12,"PNG tronqué.");int size=checked((int)BinaryPrimitives.ReadUInt32BigEndian(b.AsSpan(offset,4)));Need(size<=b.Length-offset-12,"PNG tronqué.");
            var kind=b.AsSpan(offset+4,4);var data=b.AsSpan(offset+8,size);Need(Crc(b.AsSpan(offset+4,size+4))==BinaryPrimitives.ReadUInt32BigEndian(b.AsSpan(offset+8+size,4)),"CRC PNG invalide.");
            Need(!kind.SequenceEqual("acTL"u8) && !kind.SequenceEqual("fcTL"u8) && !kind.SequenceEqual("fdAT"u8), "PNG animé non supporté ; utilise une image statique.");
            if(!header) {
                Need(kind.SequenceEqual("IHDR"u8)&&size==13,"En-tête PNG invalide.");width=checked((int)BinaryPrimitives.ReadUInt32BigEndian(data));height=checked((int)BinaryPrimitives.ReadUInt32BigEndian(data[4..]));Dimensions(width,height);
                Need(data[8]==8&&data[10]==0&&data[11]==0&&data[12]==0,"PNG accepté : 8 bits, non entrelacé.");channels=data[9] switch {0=>1,2=>3,3=>1,4=>2,6=>4,_=>0};Need(channels>0,"Couleurs PNG non supportées.");header=true;
            } else if(kind.SequenceEqual("IHDR"u8)) Need(false,"En-tête PNG répété.");
            else if(kind.SequenceEqual("PLTE"u8)) {Need(size>=3&&size<=768&&size%3==0,"Palette PNG invalide.");palette=true;}
            else if(kind.SequenceEqual("IDAT"u8)) packed.Write(data);
            else if(kind.SequenceEqual("IEND"u8)) {Need(size==0&&offset+12==b.Length,"Fin PNG invalide.");end=true;}
            else Need((kind[0]&32)!=0,"Bloc PNG critique inconnu.");
            offset+=size+12;
        }
        Need(header&&end&&packed.Length>0&&(b[25]!=3||palette),"PNG incomplet.");packed.Position=0;
        using var inflated=new ZLibStream(packed,CompressionMode.Decompress);int row=checked(width*channels+1);var buffer=new byte[row];
        for(int y=0;y<height;y++){int read=0;while(read<row){int n=inflated.Read(buffer,read,row-read);Need(n>0,"Pixels PNG tronqués.");read+=n;}Need(buffer[0]<=4,"Filtre PNG invalide.");}
        Need(inflated.ReadByte()==-1,"Pixels PNG surnuméraires.");
    }
    private static void CheckJpeg(byte[] b) {
        int pos=2;bool frame=false,scan=false,entropy=false;
        while(pos<b.Length) {
            Need(b[pos++]==255,"Marqueur JPEG invalide.");while(pos<b.Length&&b[pos]==255)pos++;Need(pos<b.Length,"JPEG tronqué.");int marker=b[pos++];
            if(marker==217){Need(frame&&scan&&entropy&&pos==b.Length,"Fin JPEG invalide.");return;}
            Need(marker!=0&&marker!=216&&marker is not (>=208 and <=215)&&pos+2<=b.Length,"Segment JPEG invalide.");int length=(b[pos]<<8)|b[pos+1];Need(length>=2&&length<=b.Length-pos,"Segment JPEG tronqué.");
            if(marker is 192 or 193 or 194) {Need(!frame&&length>=11&&b[pos+2]==8,"Image JPEG non supportée.");Dimensions((b[pos+5]<<8)|b[pos+6],(b[pos+3]<<8)|b[pos+4]);int components=b[pos+7];Need(components is 1 or 3 or 4&&length==8+3*components,"Composantes JPEG invalides.");frame=true;}
            if(marker==218) {Need(frame&&length>=6,"Balayage JPEG invalide.");scan=true;pos+=length;while(pos<b.Length){if(b[pos]!=255){entropy=true;pos++;continue;}Need(pos+1<b.Length,"JPEG tronqué.");if(b[pos+1]==0||b[pos+1] is >=208 and <=215){pos+=2;continue;}break;}}
            else pos+=length;
        }
        Need(false,"Fin JPEG absente.");
    }
    private static void CheckWebp(byte[] b) {
        Need(BinaryPrimitives.ReadUInt32LittleEndian(b.AsSpan(4,4))==b.Length-8,"Taille WebP invalide.");int pos=12;bool image=false;int canvasW=0,canvasH=0;
        while(pos<b.Length) {
            Need(b.Length-pos>=8,"WebP tronqué.");var kind=b.AsSpan(pos,4);int length=checked((int)BinaryPrimitives.ReadUInt32LittleEndian(b.AsSpan(pos+4,4)));Need(length<=b.Length-pos-8,"Bloc WebP tronqué.");var data=b.AsSpan(pos+8,length);
            if(kind.SequenceEqual("VP8X"u8)){Need(pos==12&&length==10&&(data[0]&2)==0,"WebP animé non supporté.");canvasW=1+data[4]+(data[5]<<8)+(data[6]<<16);canvasH=1+data[7]+(data[8]<<8)+(data[9]<<16);Dimensions(canvasW,canvasH);}
            else if(kind.SequenceEqual("VP8 "u8)){Need(!image&&length>10&&(data[0]&1)==0&&data.Slice(3,3).SequenceEqual(new byte[]{157,1,42}),"Image WebP VP8 invalide.");int w=BinaryPrimitives.ReadUInt16LittleEndian(data[6..])&16383,h=BinaryPrimitives.ReadUInt16LittleEndian(data[8..])&16383;Dimensions(w,h);Need(canvasW==0||(w==canvasW&&h==canvasH),"Canevas WebP divergent.");image=true;}
            else if(kind.SequenceEqual("VP8L"u8)){Need(!image&&length>5&&data[0]==47&&(data[4]&224)==0,"Image WebP VP8L invalide.");int w=1+data[1]+((data[2]&63)<<8),h=1+(data[2]>>6)+(data[3]<<2)+((data[4]&15)<<10);Dimensions(w,h);Need(canvasW==0||(w==canvasW&&h==canvasH),"Canevas WebP divergent.");image=true;}
            else Need(kind.SequenceEqual("ALPH"u8)||kind.SequenceEqual("ICCP"u8)||kind.SequenceEqual("EXIF"u8)||kind.SequenceEqual("XMP "u8),"Bloc WebP non supporté.");
            pos=checked(pos+8+length+(length&1));Need(pos<=b.Length,"Padding WebP absent.");
        }
        Need(image&&pos==b.Length,"Image WebP absente.");
    }
}
