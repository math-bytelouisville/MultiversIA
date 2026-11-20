using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using QcmStudio.Models;

namespace QcmStudio.Services;

public sealed partial class StudioStore : IDisposable {
    public const int MaximumStateBytes = 32 * 1024 * 1024;
    public static JsonSerializerOptions JsonOptions { get; } = new(JsonSerializerDefaults.Web) {
        PropertyNameCaseInsensitive = false, UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        NumberHandling = JsonNumberHandling.Strict, MaxDepth = 32, WriteIndented = true
    };
    private readonly object gate = new();
    private readonly string statePath;
    private readonly FileStream ownership;
    private readonly IPlayerProvider players;
    private readonly GameCatalog games;
    private StudioState state;

    public StudioStore(IPlayerProvider provider, GameCatalog catalog) {
        players = provider; games = catalog;
        var directory = Environment.GetEnvironmentVariable("MIA_QCM_DATA_DIR");
        if (string.IsNullOrWhiteSpace(directory) || !Path.IsPathFullyQualified(directory))
            throw new InvalidOperationException("MIA_QCM_DATA_DIR_REQUIRED: absolute persistent directory required.");
        Directory.CreateDirectory(directory); statePath = Path.Combine(directory, "studio-state.json");
        try { ownership = new FileStream(Path.Combine(directory, "studio.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException ex) { throw new InvalidOperationException("STORAGE_LOCKED: another Studio owns this data directory.", ex); }
        try {
            if (File.Exists(statePath)) {
                if (new FileInfo(statePath).Length > MaximumStateBytes) throw new InvalidDataException("State exceeds supported size.");
                state = JsonSerializer.Deserialize<StudioState>(File.ReadAllBytes(statePath), JsonOptions) ?? throw new InvalidDataException("Empty state.");
                ValidateState(state);
            } else {
                if (File.Exists(statePath + ".backup")) throw new InvalidDataException("Primary state missing while backup exists; explicit recovery required.");
                state = Seed(); Persist(state);
            }
            drafts = LoadDrafts();
        } catch (Exception ex) {
            ownership.Dispose(); throw new InvalidOperationException("STORAGE_INVALID: no automatic reset or backup replacement. " + ex.Message, ex);
        }
    }
    public void Dispose() => ownership.Dispose();
    private static T Clone<T>(T value) => JsonSerializer.Deserialize<T>(JsonSerializer.SerializeToUtf8Bytes(value, JsonOptions), JsonOptions)!;
    private static void Need(bool condition, string message) { if (!condition) throw new StudioException(400, "INVALID_INPUT", message); }
    private static void Conflict(bool condition, string code, string message) { if (condition) throw new StudioException(409, code, message); }
    private static bool Text(string? value, int maximum) => !string.IsNullOrWhiteSpace(value) && value.Length <= maximum;
    private static bool Id(string? value) => value != null && Regex.IsMatch(value, "\\A[A-Za-z0-9-]{1,80}\\z", RegexOptions.CultureInvariant);
    private static string? CreationId(string? value) {
        if (value == null) return null;
        Need(Guid.TryParseExact(value, "D", out var parsed), "requestId doit être un UUID.");
        return parsed.ToString("D");
    }
    private static string CreationHash<T>(T value) => Convert.ToHexStringLower(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(value, JsonOptions)));
    private static bool ValidCreationReceipt(string? id, string? fingerprint) => (id == null && fingerprint == null)
        || (id != null && Guid.TryParseExact(id, "D", out var parsed) && parsed.ToString("D") == id && fingerprint != null && Regex.IsMatch(fingerprint, "\\A[a-f0-9]{64}\\z", RegexOptions.CultureInvariant));
    private static void ValidateConfig(QuizConfig? config) {
        Need(config != null, "Configuration requise.");
        Need(config!.Grading is "all-or-nothing" or "partial" && config.PassingPercent is >= 0 and <= 100, "Barème ou seuil invalide.");
        Need(config.Instructions is { Length: <= 2000 }, "Consignes requises, limitées à 2 000 caractères (texte vide autorisé).");
        Need(config.Conclusion is { Length: <= 2000 }, "Conclusion requise, limitée à 2 000 caractères (texte vide autorisé).");
        Need(config.AccentColor != null && Regex.IsMatch(config.AccentColor, "\\A#[0-9A-Fa-f]{6}\\z", RegexOptions.CultureInvariant), "Couleur invalide : utilise #RRGGBB.");
        Need(config.AnswerLayout is "list" or "grid" && config.ContentWidth is "standard" or "wide" && config.TextAlign is "left" or "center", "Disposition du QCM invalide.");
    }
    private void ValidateQuiz(Quiz quiz) => MediaValidationScope(() => ValidateQuizCore(quiz));
    private void ValidateQuizCore(Quiz quiz) {
        Need(Id(quiz.Id) && Text(quiz.Title, 120) && quiz.Revision >= 1, "Identité, titre ou révision du QCM invalide.");
        Need(ValidCreationReceipt(quiz.CreationRequestId, quiz.CreationFingerprint), "Reçu de création du QCM invalide.");
        ValidateConfig(quiz.Config);
        Need(quiz.Questions is { Count: >= 1 and <= 50 }, "Le QCM doit avoir de 1 à 50 questions.");
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var question in quiz.Questions!) {
            Need(question != null, "Question requise."); var q = question!;
            Need(Id(q.Id) && ids.Add(q.Id) && Text(q.Text, 500) && q.Mode is "single" or "multiple", "Question invalide ou identifiant dupliqué.");
            Need(q.Explanation != null && q.Explanation.Length <= 1000, "Explication trop longue ou absente.");
            Need(q.Options is { Count: >= 2 and <= 8 }, "Chaque question doit proposer de 2 à 8 options.");
            ValidateImage(q.Image);
            foreach (var option in q.Options!) {
                Need(option != null && Id(option.Id) && ids.Add(option.Id) && option.Text is { Length: <= 200 } && (Text(option.Text, 200) || option.Image != null), "Une réponse nécessite un texte ou une image décrite, avec un identifiant unique.");
                ValidateImage(option!.Image);
            }
            var correct = q.Options.Count(o => o.Correct);
            Need(q.Mode == "single" ? correct == 1 : correct >= 1, "Choisis exactement une bonne option en réponse unique, au moins une en réponse multiple.");
        }
    }
    private static void ValidateAnswers(Quiz quiz, List<Answer>? answers, bool complete) {
        Need(answers != null && answers.Count <= quiz.Questions.Count, "Liste de réponses invalide."); var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var answer in answers!) {
            Need(answer != null && ids.Add(answer.QuestionId), "Réponse manquante ou question répétée."); var a = answer!;
            var question = quiz.Questions.Find(q => q.Id == a.QuestionId); Need(question != null, "Question inconnue dans cette tentative.");
            Need(a.OptionIds is { Count: >= 1 and <= 8 } && a.OptionIds.Distinct(StringComparer.Ordinal).Count() == a.OptionIds.Count, "Choix vides ou répétés ; omets les questions non répondues.");
            Need(a.OptionIds!.All(id => question!.Options.Any(o => o.Id == id)), "Option inconnue dans cette tentative.");
            Need(question!.Mode != "single" || a.OptionIds.Count == 1, "Une seule réponse autorisée pour cette question.");
        }
        Need(!complete || quiz.Config.AllowUnanswered || answers!.Count == quiz.Questions.Count, "Réponds à toutes les questions avant de terminer.");
    }
    private static string Fingerprint(string quizId, long revision) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(quizId + "\n" + revision.ToString(System.Globalization.CultureInfo.InvariantCulture))));
    // Historical media can disappear without invalidating scores or blocking unrelated commits.
    // New quiz/draft content is validated strictly at its input boundary.
    private void ValidateState(StudioState value) => MediaValidationScope(() => ValidateStateCore(value), false);
    private void ValidateStateCore(StudioState value) {
        Need(value.SchemaVersion == "mia-qcm-studio-state-1" && value.Revision >= 1, "Version du fichier d’état invalide.");
        Need(value.Account != null && Id(value.Account.Id) && Text(value.Account.DisplayName, 60), "Compte local invalide.");
        Need(value.Quizzes != null && value.QuizHistory != null && value.Attempts != null, "Collections de l’état absentes.");
        Need(value.Quizzes!.Count <= 200 && value.QuizHistory!.Count <= 2000 && value.Attempts!.Count <= 2000, "Limite de stockage atteinte.");
        Need(value.Quizzes.Select(q => q.Id).Distinct().Count() == value.Quizzes.Count, "QCM dupliqué.");
        Need(value.Quizzes.Where(q => q.CreationRequestId != null).Select(q => q.CreationRequestId).Distinct().Count() == value.Quizzes.Count(q => q.CreationRequestId != null), "Reçu de création QCM dupliqué.");
        foreach (var quiz in value.Quizzes.Concat(value.QuizHistory!)) ValidateQuiz(quiz);
        var requests = new HashSet<string>(StringComparer.Ordinal); var attempts = new HashSet<string>(StringComparer.Ordinal);
        foreach (var attempt in value.Attempts!) {
            Need(attempt != null && Id(attempt.Id) && attempts.Add(attempt.Id), "Tentative invalide ou dupliquée."); var a = attempt!;
            Need(Guid.TryParseExact(a.RequestId, "D", out _) && requests.Add(a.RequestId), "Reçu de création invalide ou dupliqué.");
            Need(a.Snapshot != null && a.Player != null && Id(a.Player.Id) && Text(a.Player.DisplayName, 60), "Snapshot incomplet."); ValidateQuiz(a.Snapshot!);
            Need(a.RequestFingerprint == Fingerprint(a.Snapshot!.Id, a.Snapshot.Revision) && !a.Snapshot.Archived, "Lien du reçu au snapshot invalide.");
            Need(a.Revision >= 1 && a.Status is "in-progress" or "completed", "État de tentative invalide.");
            Need(a.Status == "completed" ? a.CompletedAt != null && a.CompletedAt >= a.StartedAt : a.CompletedAt == null, "Dates de tentative incohérentes.");
            ValidateAnswers(a.Snapshot, a.Answers, a.Status == "completed");
        }
    }
    private void Persist(StudioState next) {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(next, JsonOptions);
        Conflict(bytes.Length > MaximumStateBytes, "STORAGE_LIMIT", "Stockage maximal atteint ; aucun historique n’a été effacé.");
        WriteWithBackup(statePath, bytes);
    }
    private static void WriteWithBackup(string path, byte[] bytes) {
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".pending";
        using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { file.Write(bytes); file.Flush(true); }
        if (File.Exists(path)) File.Replace(temporary, path, path + ".backup"); else File.Move(temporary, path);
    }
    private void Commit(StudioState next) { next.Revision = checked(state.Revision + 1); ValidateState(next); Persist(next); state = next; }
    private static Quiz FindQuiz(StudioState s, string id) => s.Quizzes.Find(q => q.Id == id) ?? throw new StudioException(404, "QUIZ_NOT_FOUND", "QCM introuvable.");
    private static Attempt FindAttempt(StudioState s, string id) => s.Attempts.Find(a => a.Id == id) ?? throw new StudioException(404, "ATTEMPT_NOT_FOUND", "Tentative introuvable.");
    private static void Revision(long requested, long actual) => Conflict(requested != actual, "REVISION_CONFLICT", "Cette version a changé. Conserve ton brouillon avant de recharger la version actuelle.");
    public object Account() { lock (gate) return players.Current(state); }
    public object Rename(AccountInput input) {
        lock (gate) { Need(Text(input.DisplayName, 60), "Le nom doit contenir de 1 à 60 caractères."); var next = Clone(state); players.Rename(next, input.DisplayName.Trim()); Commit(next); return players.Current(state); }
    }
    public object Studio() {
        lock (gate) return new { account = players.Current(state), games = games.Entries(),
            drafts = drafts.Select(d => new { d.Id, d.Name, d.Revision }).ToArray(),
            quizzes = state.Quizzes.Select(q => new { q.Id, q.Title, q.Revision, questionCount = q.Questions.Count, q.Config, q.Archived, q.UpdatedAt }).ToArray(),
            attempts = state.Attempts.OrderByDescending(a => a.StartedAt).Select(Summary).ToArray() };
    }
    public object Quiz(string id) { lock (gate) return FindQuiz(state, id); }
    public object CreateQuiz(QuizInput input, string? requestId = null) {
        lock (gate) {
            var request = CreationId(requestId); var fingerprint = request == null ? null : CreationHash<QuizInput>(input);
            var replay = request == null ? null : state.Quizzes.Find(q => q.CreationRequestId == request);
            if (replay != null) { Conflict(replay.CreationFingerprint != fingerprint, "REQUEST_CONFLICT", "Cette clé de création appartient à un autre contenu."); return replay; }
            Conflict(state.Quizzes.Count >= 200, "STORAGE_LIMIT", "Nombre maximal de QCM atteint ; historique préservé.");
            var quiz = new Quiz { Id = Guid.NewGuid().ToString("D"), Title = input.Title, Questions = input.Questions, Config = input.Config, Revision = 1, Archived = false, UpdatedAt = DateTimeOffset.UtcNow, CreationRequestId = request, CreationFingerprint = fingerprint };
            ValidateQuiz(quiz); var next = Clone(state); next.Quizzes.Add(Clone(quiz)); Commit(next); return quiz;
        }
    }
    public object UpdateQuiz(string id, QuizUpdate input) {
        lock (gate) {
            var old = FindQuiz(state, id); Revision(input.Revision, old.Revision); Conflict(old.Archived, "QUIZ_ARCHIVED", "Un QCM archivé ne peut plus être modifié.");
            Conflict(state.QuizHistory.Count >= 2000, "STORAGE_LIMIT", "Nombre maximal de versions atteint ; historique préservé.");
            var quiz = new Quiz { Id = id, Title = input.Title, Questions = input.Questions, Config = input.Config, Revision = checked(old.Revision + 1), Archived = false, UpdatedAt = DateTimeOffset.UtcNow, CreationRequestId = old.CreationRequestId, CreationFingerprint = old.CreationFingerprint };
            ValidateQuiz(quiz); var next = Clone(state); next.QuizHistory.Add(Clone(old)); next.Quizzes[next.Quizzes.FindIndex(q => q.Id == id)] = Clone(quiz); Commit(next); return quiz;
        }
    }
    public object ArchiveQuiz(string id, RevisionInput input) {
        lock (gate) {
            var old = FindQuiz(state, id); Revision(input.Revision, old.Revision); if (old.Archived) return old;
            Conflict(state.QuizHistory.Count >= 2000, "STORAGE_LIMIT", "Nombre maximal de versions atteint ; historique préservé.");
            var next = Clone(state); next.QuizHistory.Add(Clone(old)); var quiz = FindQuiz(next, id); quiz.Archived = true; quiz.Revision++; quiz.UpdatedAt = DateTimeOffset.UtcNow; Commit(next); return quiz;
        }
    }
    public (object Value, bool Created) Start(StartInput input) {
        lock (gate) {
            Need(Guid.TryParseExact(input.RequestId, "D", out var requestId), "requestId doit être un UUID."); Need(Id(input.QuizId) && input.QuizRevision >= 1, "Identité ou révision de QCM invalide.");
            var request = requestId.ToString("D"); var fingerprint = Fingerprint(input.QuizId, input.QuizRevision); var replay = state.Attempts.Find(a => a.RequestId == request);
            if (replay != null) { Conflict(replay.RequestFingerprint != fingerprint, "REQUEST_CONFLICT", "Cette clé de création appartient à une autre demande."); return (AttemptView(replay), false); }
            var quiz = FindQuiz(state, input.QuizId); Revision(input.QuizRevision, quiz.Revision); Conflict(quiz.Archived, "QUIZ_ARCHIVED", "Ce QCM est archivé ; ses anciennes tentatives restent accessibles.");
            Conflict(state.Attempts.Count >= 2000, "STORAGE_LIMIT", "Nombre maximal de tentatives atteint ; historique préservé.");
            var snapshot = Clone(quiz); if (snapshot.Config.ShuffleQuestions) Shuffle(snapshot.Questions); if (snapshot.Config.ShuffleOptions) foreach (var q in snapshot.Questions) Shuffle(q.Options);
            var attempt = new Attempt { Id = Guid.NewGuid().ToString("D"), RequestId = request, RequestFingerprint = fingerprint, Snapshot = snapshot,
                Player = players.Current(state), Status = "in-progress", Revision = 1, Answers = [], StartedAt = DateTimeOffset.UtcNow, CompletedAt = null };
            var next = Clone(state); next.Attempts.Add(attempt); Commit(next); return (AttemptView(attempt), true);
        }
    }
    private static void Shuffle<T>(List<T> values) { for (var i = values.Count - 1; i > 0; i--) { var j = RandomNumberGenerator.GetInt32(i + 1); (values[i], values[j]) = (values[j], values[i]); } }
    public object Attempt(string id) { lock (gate) return AttemptView(FindAttempt(state, id)); }
    public object Answers(string id, AnswersInput input) {
        lock (gate) {
            var old = FindAttempt(state, id); Conflict(old.Status == "completed", "ATTEMPT_COMPLETED", "Cette tentative terminée est verrouillée."); Revision(input.Revision, old.Revision);
            ValidateAnswers(old.Snapshot, input.Answers, false); var next = Clone(state); var attempt = FindAttempt(next, id); attempt.Answers = Clone(input.Answers); attempt.Revision++; Commit(next); return AttemptView(attempt);
        }
    }
    public object Complete(string id, RevisionInput input) {
        lock (gate) {
            var old = FindAttempt(state, id); if (old.Status == "completed") return AttemptView(old);
            Revision(input.Revision, old.Revision); ValidateAnswers(old.Snapshot, old.Answers, true);
            var next = Clone(state); var attempt = FindAttempt(next, id); attempt.Status = "completed"; attempt.CompletedAt = DateTimeOffset.UtcNow; attempt.Revision++; Commit(next); return AttemptView(attempt);
        }
    }
    private static Fraction Points(Question q, Answer answer, string grading) {
        var good = q.Options.Where(o => o.Correct).Select(o => o.Id).ToHashSet(StringComparer.Ordinal);
        if (good.SetEquals(answer.OptionIds)) return Fraction.One;
        if (q.Mode == "single" || grading == "all-or-nothing") return Fraction.Zero;
        var selectedGood = answer.OptionIds.Count(good.Contains); var badCount = q.Options.Count - good.Count;
        var points = new Fraction(selectedGood, good.Count) - (badCount == 0 ? Fraction.Zero : new Fraction(answer.OptionIds.Count - selectedGood, badCount));
        return points.N < 0 ? Fraction.Zero : points;
    }
    private static (Fraction Score, Fraction Percent, int Correct) Calculate(Attempt a) {
        var sum = Fraction.Zero; var correct = 0;
        foreach (var q in a.Snapshot.Questions) { var answer = a.Answers.SingleOrDefault(x => x.QuestionId == q.Id); if (answer == null) continue; sum += Points(q, answer, a.Snapshot.Config.Grading); if (q.Options.Where(o => o.Correct).Select(o => o.Id).ToHashSet().SetEquals(answer.OptionIds)) correct++; }
        return (sum, sum.Times(100, a.Snapshot.Questions.Count), correct);
    }
    private static object Result(Attempt a) {
        var score = Calculate(a);
        return new { score = score.Score.Rounded(), maxScore = a.Snapshot.Questions.Count, percent = score.Percent.Rounded(),
            passed = score.Percent.CompareTo(Fraction.OfDecimal(a.Snapshot.Config.PassingPercent)) >= 0, correctCount = score.Correct,
            details = a.Snapshot.Config.ShowCorrection ? a.Snapshot.Questions.Select(q => {
                var answer = a.Answers.SingleOrDefault(x => x.QuestionId == q.Id) ?? new Answer { QuestionId = q.Id, OptionIds = [] }; var correct = q.Options.Where(o => o.Correct).Select(o => o.Id).ToArray();
                return (object)new { questionId = q.Id, points = Points(q, answer, a.Snapshot.Config.Grading).Rounded(), maxPoints = 1,
                    isCorrect = correct.ToHashSet().SetEquals(answer.OptionIds), selectedOptionIds = answer.OptionIds, correctOptionIds = correct, explanation = q.Explanation };
            }).ToArray() : Array.Empty<object>() };
    }
    private static object Summary(Attempt a) => new { a.Id, quizId = a.Snapshot.Id, quizTitle = a.Snapshot.Title, quizRevision = a.Snapshot.Revision,
        a.Status, a.Revision, answeredCount = a.Answers.Count, questionCount = a.Snapshot.Questions.Count, a.StartedAt, a.CompletedAt, result = a.Status == "completed" ? Result(a) : null };
    private static object AttemptView(Attempt a) => new { a.Id, quizId = a.Snapshot.Id, quizTitle = a.Snapshot.Title, quizRevision = a.Snapshot.Revision,
        a.Status, a.Revision, player = a.Player, a.Snapshot.Config, a.Answers, a.StartedAt, a.CompletedAt,
        questions = a.Snapshot.Questions.Select(q => new { q.Id, q.Text, q.Mode, q.Image, options = q.Options.Select(o => new { o.Id, o.Text, o.Image }).ToArray() }).ToArray(), result = a.Status == "completed" ? Result(a) : null };
    public object Scores() {
        lock (gate) return new { groups = state.Attempts.Where(a => a.Status == "completed")
            .GroupBy(a => (a.Snapshot.Id, a.Snapshot.Revision, a.Snapshot.Config.Grading)).Select(group => new {
                quizId = group.Key.Id, quizTitle = group.First().Snapshot.Title, quizRevision = group.Key.Revision, grading = group.Key.Grading,
                entries = group.OrderByDescending(a => Calculate(a).Percent).ThenBy(a => a.CompletedAt).ThenBy(a => a.Id, StringComparer.Ordinal).Select(a => {
                    var score = Calculate(a); return new { attemptId = a.Id, playerName = a.Player.DisplayName, score = score.Score.Rounded(), maxScore = a.Snapshot.Questions.Count,
                        percent = score.Percent.Rounded(), passed = score.Percent.CompareTo(Fraction.OfDecimal(a.Snapshot.Config.PassingPercent)) >= 0, a.CompletedAt };
                }).ToArray()
            }).ToArray() };
    }
    private static StudioState Seed() {
        Question Q(string id, string text, string[] options, int correct) => new() { Id = id, Text = text, Mode = "single", Explanation = "",
            Options = options.Select((text, i) => new QuizOption { Id = id + "-o" + i, Text = text, Correct = i == correct }).ToList() };
        return new StudioState { SchemaVersion = "mia-qcm-studio-state-1", Revision = 1, Account = new Player { Id = "local-player", DisplayName = "Joueur local" }, QuizHistory = [], Attempts = [],
            Quizzes = [new Quiz { Id = "seed-maths", Title = "Le petit QCM des maths", Revision = 1, Archived = false, UpdatedAt = DateTimeOffset.UtcNow,
                Config = new QuizConfig { Grading = "all-or-nothing", PassingPercent = 60, ShuffleQuestions = false, ShuffleOptions = false, ShowCorrection = true },
                Questions = [Q("q1", "Combien font 2 + 3 ?", ["4", "5", "6", "7"], 1), Q("q2", "Quel est le carré de 4 ?", ["8", "12", "16", "20"], 2), Q("q3", "Combien de côtés possède un triangle ?", ["2", "3", "4", "5"], 1)] }] };
    }
}
