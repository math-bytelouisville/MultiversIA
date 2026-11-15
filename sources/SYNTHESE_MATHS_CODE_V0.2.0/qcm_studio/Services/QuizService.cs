using QcmDemo.Models;

namespace QcmDemo.Services;

public sealed class QuizService(QuizCatalog catalog)
{
    public PublicQuiz Questions() => catalog.Public();
    public GradeResult Grade(int a, int b, int c)
    {
        var expected = catalog.Data.Questions.Select(q => q.Correct).ToArray();
        bool[] correct = [QuizRules.IsCorrect(a, expected[0]), QuizRules.IsCorrect(b, expected[1]), QuizRules.IsCorrect(c, expected[2])];
        var score = QuizRules.Score(correct[0], correct[1], correct[2]);
        return new GradeResult(score, 3, QuizRules.Passed(score), correct, expected);
    }
}
