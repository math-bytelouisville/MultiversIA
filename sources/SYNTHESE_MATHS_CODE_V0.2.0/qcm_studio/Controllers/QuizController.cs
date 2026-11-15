using Microsoft.AspNetCore.Http.Features;
using Microsoft.AspNetCore.Mvc;
using QcmDemo.Services;

namespace QcmDemo.Controllers;

public sealed class QuizController(QuizService quiz) : Controller
{
    [HttpGet("/")]
    public IActionResult Index() => View(quiz.Questions());

    [HttpGet("/api/meta")]
    public IActionResult Meta() => Json(new { language = "C#", framework = "ASP.NET Core MVC", profile = "qcm-mvc-1" });

    [HttpGet("/api/quiz/questions")]
    public IActionResult Questions() => Json(quiz.Questions());

    [HttpGet("/api/quiz/grade")]
    public IActionResult Grade()
    {
        // Read the raw query: no percent-decoding, case folding or duplicate collapse.
        var raw = HttpContext.Features.Get<IHttpRequestFeature>()?.RawTarget ?? "";
        var start = raw.IndexOf('?');
        if (start < 0) return BadRequest(new { error = "INVALID_ANSWERS" });
        var parts = raw[(start + 1)..].Split('&');
        var values = new Dictionary<char, int>();
        if (parts.Length != 3) return BadRequest(new { error = "INVALID_ANSWERS" });
        foreach (var part in parts) {
            if (part.Length != 3 || part[1] != '=' || part[0] < 'a' || part[0] > 'c' || part[2] < '0' || part[2] > '3' ||
                !values.TryAdd(part[0], part[2] - '0')) return BadRequest(new { error = "INVALID_ANSWERS" });
        }
        return Json(quiz.Grade(values['a'], values['b'], values['c']));
    }
}
