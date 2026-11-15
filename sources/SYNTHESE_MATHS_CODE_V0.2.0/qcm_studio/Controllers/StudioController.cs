using Microsoft.AspNetCore.Mvc;
using QcmStudio.Models;
using QcmStudio.Services;

namespace QcmStudio.Controllers;

public sealed class StudioController : Controller {
    [HttpGet("/")]
    public IActionResult Index() => View("~/Views/Studio/Index.cshtml");
}

[ApiController]
[Route("api")]
public sealed class StudioApiController(StudioStore store) : ControllerBase {
    [HttpGet("studio")] public object Studio() => store.Studio();
    [HttpGet("account")] public object Account() => store.Account();
    [HttpPut("account")] public object Account(AccountInput input) => store.Rename(input);
    [HttpGet("quizzes/{id}")] public object Quiz(string id) => store.Quiz(id);
    [HttpPost("quizzes")] public IActionResult Create(QuizInput input) => StatusCode(201, store.CreateQuiz(input));
    [HttpPut("quizzes/{id}")] public object Update(string id, QuizUpdate input) => store.UpdateQuiz(id, input);
    [HttpPost("quizzes/{id}/archive")] public object Archive(string id, RevisionInput input) => store.ArchiveQuiz(id, input);
    [HttpPost("attempts")] public IActionResult Start(StartInput input) { var result = store.Start(input); return StatusCode(result.Created ? 201 : 200, result.Value); }
    [HttpGet("attempts/{id}")] public object Attempt(string id) => store.Attempt(id);
    [HttpPut("attempts/{id}/answers")] public object Answers(string id, AnswersInput input) => store.Answers(id, input);
    [HttpPost("attempts/{id}/complete")] public object Complete(string id, RevisionInput input) => store.Complete(id, input);
    [HttpGet("scores")] public object Scores() => store.Scores();
}
