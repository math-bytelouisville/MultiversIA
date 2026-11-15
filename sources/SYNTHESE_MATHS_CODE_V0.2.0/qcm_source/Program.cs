using System.Net;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http.Features;
using QcmDemo.Services;

var builder = WebApplication.CreateBuilder(new WebApplicationOptions {
    Args = [], ContentRootPath = AppContext.BaseDirectory
});
builder.Logging.ClearProviders();
builder.WebHost.ConfigureKestrel(options => options.Listen(IPAddress.Loopback, 0));
builder.Services.AddControllersWithViews();
builder.Services.AddSingleton<QuizCatalog>();
builder.Services.AddSingleton<QuizService>();
var app = builder.Build();
_ = app.Services.GetRequiredService<QuizCatalog>();

string[] routes = ["/", "/style.css", "/app.js", "/api/meta", "/api/quiz/questions", "/api/quiz/grade"];
app.Use(async (context, next) => {
    var raw = context.Features.Get<IHttpRequestFeature>()?.RawTarget ?? "";
    var split = raw.IndexOf('?');
    var path = split < 0 ? raw : raw[..split];
    if (path.StartsWith("/api/", StringComparison.Ordinal)) context.Response.Headers.CacheControl = "no-store";
    if (!routes.Contains(path, StringComparer.Ordinal)) {
        context.Response.StatusCode = 404;
        return;
    }
    if (!HttpMethods.IsGet(context.Request.Method)) {
        context.Response.StatusCode = 405;
        context.Response.Headers.Allow = "GET";
        return;
    }
    if (path != "/api/quiz/grade" && split >= 0) {
        context.Response.StatusCode = 400;
        await context.Response.WriteAsJsonAsync(new { error = "INVALID_ANSWERS" });
        return;
    }
    await next(context);
});
app.UseStaticFiles();
app.MapControllers();
app.Lifetime.ApplicationStarted.Register(() => {
    var addresses = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>();
    var address = addresses?.Addresses.Single() ?? throw new InvalidOperationException("Bound address unavailable.");
    Console.WriteLine("READY " + new Uri(address).Port);
    Console.Out.Flush();
});
app.Run();
