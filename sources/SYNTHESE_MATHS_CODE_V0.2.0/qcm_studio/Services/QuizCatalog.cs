using System.Text.Json;
using QcmDemo.Models;

namespace QcmDemo.Services;

public sealed class QuizCatalog
{
    public QuizData Data { get; }
    public QuizCatalog(IWebHostEnvironment environment)
    {
        var path = Path.Combine(environment.ContentRootPath, "Data", "questions.json");
        Data = JsonSerializer.Deserialize<QuizData>(File.ReadAllBytes(path), new JsonSerializerOptions {
            PropertyNameCaseInsensitive = true
        }) ?? throw new InvalidDataException("Quiz absent.");
        if (string.IsNullOrWhiteSpace(Data.Title) || Data.Questions is null || Data.Questions.Length != 3)
            throw new InvalidDataException("This profile requires three questions.");
        for (var i = 0; i < 3; i++) {
            var question = Data.Questions[i];
            if (question is null || question.Id != "q" + (i + 1) || string.IsNullOrWhiteSpace(question.Text) ||
                question.Options is null || question.Options.Length != 4 ||
                question.Options.Any(string.IsNullOrWhiteSpace) || question.Correct < 0 || question.Correct > 3)
                throw new InvalidDataException("Invalid question profile.");
        }
    }
    public PublicQuiz Public() => new(Data.Title, Data.Questions.Select(q => new PublicQuestion(q.Id, q.Text, q.Options.ToArray())).ToArray());
}
