namespace QcmStudio.Models;

public sealed class Player {
    public required string Id { get; set; }
    public required string DisplayName { get; set; }
}
public sealed class QuizOption {
    public required string Id { get; set; }
    public required string Text { get; set; }
    public required bool Correct { get; set; }
    public QuizImage? Image { get; set; }
}
public sealed class Question {
    public required string Id { get; set; }
    public required string Text { get; set; }
    public required string Mode { get; set; }
    public required List<QuizOption> Options { get; set; }
    public required string Explanation { get; set; }
    public QuizImage? Image { get; set; }
}
public sealed class QuizImage {
    public required string Id { get; set; }
    public required string Alt { get; set; }
    public string Position { get; set; } = "above";
    public string Size { get; set; } = "medium";
    public string Fit { get; set; } = "contain";
}
public sealed class MediaInput { public required string DataUrl { get; set; } }
public sealed class QuizConfig {
    public required string Grading { get; set; }
    public required decimal PassingPercent { get; set; }
    public required bool ShuffleQuestions { get; set; }
    public required bool ShuffleOptions { get; set; }
    public required bool ShowCorrection { get; set; }
    public string Instructions { get; set; } = "";
    public string Conclusion { get; set; } = "";
    public string AccentColor { get; set; } = "#397866";
    public bool AllowUnanswered { get; set; } = false;
    public string AnswerLayout { get; set; } = "list";
    public string ContentWidth { get; set; } = "standard";
    public string TextAlign { get; set; } = "left";
}
public sealed class Quiz {
    public required string Id { get; set; }
    public required string Title { get; set; }
    public required long Revision { get; set; }
    public required List<Question> Questions { get; set; }
    public required QuizConfig Config { get; set; }
    public required bool Archived { get; set; }
    public required DateTimeOffset UpdatedAt { get; set; }
    public string? CreationRequestId { get; set; }
    public string? CreationFingerprint { get; set; }
}
public sealed class Answer {
    public required string QuestionId { get; set; }
    public required List<string> OptionIds { get; set; }
}
public sealed class Attempt {
    public required string Id { get; set; }
    public required string RequestId { get; set; }
    public required string RequestFingerprint { get; set; }
    public required Quiz Snapshot { get; set; }
    public required Player Player { get; set; }
    public required string Status { get; set; }
    public required long Revision { get; set; }
    public required List<Answer> Answers { get; set; }
    public required DateTimeOffset StartedAt { get; set; }
    public required DateTimeOffset? CompletedAt { get; set; }
}
public sealed class StudioState {
    public required string SchemaVersion { get; set; }
    public required long Revision { get; set; }
    public required Player Account { get; set; }
    public required List<Quiz> Quizzes { get; set; }
    public required List<Quiz> QuizHistory { get; set; }
    public required List<Attempt> Attempts { get; set; }
}
public sealed class AccountInput { public required string DisplayName { get; set; } }
public class QuizInput {
    public required string Title { get; set; }
    public required List<Question> Questions { get; set; }
    public required QuizConfig Config { get; set; }
}
public sealed class QuizUpdate : QuizInput { public required long Revision { get; set; } }
public sealed class QuizCreate : QuizInput { public string? RequestId { get; set; } }
public sealed class QuizDraft {
    public required string Id { get; set; }
    public required long Revision { get; set; }
    public required string Name { get; set; }
    public required QuizInput Content { get; set; }
}
public class DraftInput {
    public required string Name { get; set; }
    public required QuizInput Content { get; set; }
}
public sealed class DraftUpdate : DraftInput { public required long Revision { get; set; } }
public sealed class DraftCreate : DraftInput { public string? RequestId { get; set; } }
public sealed class DraftCreationReceipt {
    public required string RequestId { get; set; }
    public required string Fingerprint { get; set; }
    public required string DraftId { get; set; }
}
public sealed class DraftFile {
    public required string SchemaVersion { get; set; }
    public required List<QuizDraft> Drafts { get; set; }
    public required List<DraftCreationReceipt> CreationReceipts { get; set; }
}
public sealed class DraftDelete {
    public required long Revision { get; set; }
    public required string Name { get; set; }
}
public sealed class RevisionInput { public required long Revision { get; set; } }
public sealed class StartInput {
    public required string QuizId { get; set; }
    public required long QuizRevision { get; set; }
    public required string RequestId { get; set; }
}
public sealed class AnswersInput {
    public required long Revision { get; set; }
    public required List<Answer> Answers { get; set; }
}
