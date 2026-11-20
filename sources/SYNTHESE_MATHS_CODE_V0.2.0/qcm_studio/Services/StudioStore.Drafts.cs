using System.Text.Json;
using QcmStudio.Models;

namespace QcmStudio.Services;

public sealed partial class StudioStore {
    private const int MaximumDrafts = 100;
    private const int MaximumDraftReceipts = 2000;
    private List<QuizDraft> drafts;
    private List<DraftCreationReceipt> draftCreationReceipts = [];
    private string DraftPath => Path.Combine(Path.GetDirectoryName(statePath)!, "studio-drafts.json");

    private void ValidateDraft(string name, QuizInput? content) => MediaValidationScope(() => ValidateDraftCore(name, content));
    private void ValidateDraftCore(string name, QuizInput? content) {
        Need(Text(name, 100), "Donne au brouillon un nom de 1 à 100 caractères.");
        Need(content is { Title.Length: <= 120, Questions.Count: >= 1 and <= 50, Config: not null }, "Contenu du brouillon invalide.");
        var quiz = content!;
        ValidateConfig(quiz.Config);
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var question in quiz.Questions) {
            Need(question is { Text.Length: <= 500, Explanation.Length: <= 1000, Options.Count: >= 2 and <= 8 } &&
                question.Mode is "single" or "multiple" && Id(question.Id) && ids.Add(question.Id), "Question du brouillon invalide.");
            ValidateImage(question!.Image);
            foreach (var option in question.Options) {
                Need(option is { Text.Length: <= 200 } && Id(option.Id) && ids.Add(option.Id), "Option du brouillon invalide.");
                ValidateImage(option!.Image);
            }
        }
    }

    private List<QuizDraft> LoadDrafts() {
        List<QuizDraft>? result = null;
        MediaValidationScope(() => result = LoadDraftsCore(), false);
        return result!;
    }
    private List<QuizDraft> LoadDraftsCore() {
        var path = DraftPath;
        if (!File.Exists(path)) {
            if (File.Exists(path + ".backup")) throw new InvalidDataException("Sauvegarde des brouillons à restaurer explicitement.");
            return [];
        }
        if (new FileInfo(path).Length > MaximumStateBytes) throw new InvalidDataException("Fichier des brouillons trop volumineux.");
        var bytes = File.ReadAllBytes(path);
        using var document = JsonDocument.Parse(bytes);
        List<QuizDraft> loaded;
        if (document.RootElement.ValueKind == JsonValueKind.Array) {
            loaded = JsonSerializer.Deserialize<List<QuizDraft>>(bytes, JsonOptions) ?? throw new InvalidDataException("Fichier des brouillons vide.");
            draftCreationReceipts = [];
        } else {
            var saved = JsonSerializer.Deserialize<DraftFile>(bytes, JsonOptions);
            Need(saved != null && saved.SchemaVersion == "mia-qcm-drafts-2" && saved.Drafts != null && saved.CreationReceipts != null, "Format de brouillons invalide.");
            loaded = saved!.Drafts!; draftCreationReceipts = saved.CreationReceipts!;
        }
        Need(draftCreationReceipts.Count <= MaximumDraftReceipts, "Limite des reçus de brouillons dépassée.");
        var requests = new HashSet<string>(StringComparer.Ordinal); var createdIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var receipt in draftCreationReceipts) {
            Need(receipt != null && receipt.RequestId != null && ValidCreationReceipt(receipt.RequestId, receipt.Fingerprint)
                 && requests.Add(receipt.RequestId) && Id(receipt.DraftId) && createdIds.Add(receipt.DraftId), "Reçu de création de brouillon invalide ou répété.");
        }
        Need(loaded.Count <= MaximumDrafts, "Limite de brouillons dépassée.");
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var draft in loaded) {
            Need(draft != null && Id(draft.Id) && ids.Add(draft.Id) && draft.Revision > 0, "Identité du brouillon invalide.");
            ValidateDraft(draft!.Name, draft.Content);
        }
        return loaded;
    }

    private void SaveDrafts(List<QuizDraft> next, List<DraftCreationReceipt>? receipts = null) {
        var nextReceipts = receipts ?? draftCreationReceipts;
        var bytes = JsonSerializer.SerializeToUtf8Bytes(new DraftFile { SchemaVersion = "mia-qcm-drafts-2", Drafts = next, CreationReceipts = nextReceipts }, JsonOptions);
        Conflict(bytes.Length > MaximumStateBytes, "STORAGE_LIMIT", "Limite de stockage des brouillons atteinte.");
        WriteWithBackup(DraftPath, bytes);
        drafts = next;
        draftCreationReceipts = nextReceipts;
    }

    private QuizDraft FindDraft(string id) => drafts.Find(d => d.Id == id)
        ?? throw new StudioException(404, "DRAFT_NOT_FOUND", "Brouillon introuvable.");

    public QuizDraft Draft(string id) { lock (gate) return FindDraft(id); }

    public QuizDraft CreateDraft(DraftInput input, string? requestId = null) {
        lock (gate) {
            var request = CreationId(requestId); var fingerprint = request == null ? null : CreationHash<DraftInput>(input);
            var receipt = request == null ? null : draftCreationReceipts.Find(r => r.RequestId == request);
            if (receipt != null) {
                Conflict(receipt.Fingerprint != fingerprint, "REQUEST_CONFLICT", "Cette clé de création appartient à un autre contenu.");
                return drafts.Find(d => d.Id == receipt.DraftId) ?? throw new StudioException(410, "DRAFT_DELETED", "Ce brouillon a déjà été créé puis supprimé. Il ne sera pas recréé par cet envoi.");
            }
            ValidateDraft(input.Name, input.Content);
            Conflict(drafts.Count >= MaximumDrafts, "STORAGE_LIMIT", "Nombre maximal de brouillons atteint.");
            Conflict(request != null && draftCreationReceipts.Count >= MaximumDraftReceipts, "STORAGE_LIMIT", "Limite des reçus de création atteinte ; aucun reçu ancien n’a été effacé.");
            var draft = new QuizDraft { Id = Guid.NewGuid().ToString("D"), Name = input.Name.Trim(), Content = input.Content, Revision = 1 };
            List<DraftCreationReceipt> nextReceipts = request == null ? draftCreationReceipts : [.. draftCreationReceipts, new DraftCreationReceipt { RequestId = request, Fingerprint = fingerprint!, DraftId = draft.Id }];
            SaveDrafts([.. drafts, draft], nextReceipts);
            return draft;
        }
    }

    public QuizDraft UpdateDraft(string id, DraftUpdate input) {
        lock (gate) {
            var previous = FindDraft(id);
            Revision(input.Revision, previous.Revision);
            ValidateDraft(input.Name, input.Content);
            var draft = new QuizDraft { Id = id, Name = input.Name.Trim(), Content = input.Content, Revision = checked(previous.Revision + 1) };
            SaveDrafts(drafts.Select(d => d.Id == id ? draft : d).ToList());
            return draft;
        }
    }

    public object DeleteDraft(string id, DraftDelete input) {
        lock (gate) {
            var previous = FindDraft(id);
            Revision(input.Revision, previous.Revision);
            Need(string.Equals(input.Name, previous.Name, StringComparison.Ordinal), "Retape le nom exact du brouillon pour confirmer.");
            SaveDrafts(drafts.Where(d => d.Id != id).ToList());
            return new { id };
        }
    }
}
