using System.Net;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Mvc;
using QcmStudio.Services;

var builder = WebApplication.CreateBuilder(new WebApplicationOptions {
    Args = [], ContentRootPath = AppContext.BaseDirectory
});
var port = 0;
if (builder.Environment.IsDevelopment() && Environment.GetEnvironmentVariable("MIA_QCM_DEBUG_PORT") is { } debugPort) {
    if (!ushort.TryParse(debugPort, out var parsedPort) || parsedPort == 0)
        throw new InvalidOperationException("MIA_QCM_DEBUG_PORT doit être un port TCP de 1 à 65535.");
    port = parsedPort;
    if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("MIA_QCM_DATA_DIR"))) {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        Environment.SetEnvironmentVariable("MIA_QCM_DATA_DIR", Path.Combine(localAppData, "MultiversIA", "GameEditor-Development"));
    }
}
if (Environment.GetEnvironmentVariable("MIA_QCM_PORT") is { } stablePort) {
    if (!ushort.TryParse(stablePort, out var parsedStablePort) || parsedStablePort == 0)
        throw new InvalidOperationException("MIA_QCM_PORT doit être un port TCP de 1 à 65535.");
    port = parsedStablePort;
}
builder.Logging.ClearProviders();
builder.WebHost.ConfigureKestrel(options => { options.Listen(IPAddress.Loopback, port); options.Limits.MaxRequestBodySize = 1024 * 1024; options.AddServerHeader = false; });
builder.Services.AddControllersWithViews().AddJsonOptions(options => {
    options.JsonSerializerOptions.PropertyNameCaseInsensitive = false;
    options.JsonSerializerOptions.UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow;
    options.JsonSerializerOptions.NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.Strict;
    options.JsonSerializerOptions.MaxDepth = 32;
});
builder.Services.Configure<ApiBehaviorOptions>(options => options.InvalidModelStateResponseFactory = _ =>
    new BadRequestObjectResult(new { error = "INVALID_INPUT", message = "JSON invalide, champ manquant ou champ non autorisé." }));
builder.Services.AddSingleton<IPlayerProvider, LocalPlayerProvider>();
builder.Services.AddSingleton<IGameDefinition, QcmGameDefinition>();
builder.Services.AddSingleton<GameCatalog>();
builder.Services.AddSingleton<StudioStore>();
var app = builder.Build();
_ = app.Services.GetRequiredService<StudioStore>();

app.Use(async (context, next) => {
    context.Response.Headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'none'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-src 'none'; frame-ancestors 'none'; form-action 'self'";
    context.Response.Headers["X-Frame-Options"] = "DENY";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["Referrer-Policy"] = "no-referrer";
    context.Response.Headers["Cross-Origin-Resource-Policy"] = "same-origin";
    if (context.Request.Path.StartsWithSegments("/api")) context.Response.Headers.CacheControl = "no-store";
    try {
        if (context.Request.Host.Host is not ("127.0.0.1" or "localhost")) throw new StudioException(400, "LOCAL_HOST_REQUIRED", "Ce studio est accessible uniquement sur l’hôte local.");
        if ((context.Request.Host.Port ?? 80) != context.Connection.LocalPort)
            throw new StudioException(400, "LOCAL_PORT_REQUIRED", "L’adresse demandée doit correspondre au port de ce studio.");
        if (!HttpMethods.IsGet(context.Request.Method) && !HttpMethods.IsHead(context.Request.Method)) {
            var origin = context.Request.Headers.Origin.ToString();
            var own = context.Request.Scheme + "://" + context.Request.Host.Value;
            var fetchSite = context.Request.Headers["Sec-Fetch-Site"].ToString();
            if ((origin.Length > 0 && !string.Equals(origin, own, StringComparison.OrdinalIgnoreCase)) ||
                (fetchSite.Length > 0 && fetchSite is not ("same-origin" or "none")))
                throw new StudioException(403, "SAME_ORIGIN_REQUIRED", "Les modifications doivent venir du studio ouvert sur cette même adresse locale.");
            if (!string.Equals(context.Request.ContentType?.Split(';')[0].Trim(), "application/json", StringComparison.OrdinalIgnoreCase))
                throw new StudioException(400, "JSON_REQUIRED", "Les modifications utilisent uniquement un objet JSON.");
        }
        await next(context);
    } catch (BadHttpRequestException ex) {
        context.Response.StatusCode = ex.StatusCode is >= 400 and < 500 ? ex.StatusCode : 400;
        await context.Response.WriteAsJsonAsync(new { error = "INVALID_REQUEST", message = "La requête est invalide ou dépasse la taille autorisée." });
    } catch (StudioException ex) {
        context.Response.StatusCode = ex.Status;
        await context.Response.WriteAsJsonAsync(new { error = ex.Code, message = ex.Message });
    } catch (Exception ex) {
        Console.Error.WriteLine(ex.ToString());
        if (context.Response.HasStarted) { context.Abort(); return; }
        context.Response.StatusCode = 500;
        await context.Response.WriteAsJsonAsync(new { error = "INTERNAL_ERROR", message = "L’opération n’a pas été confirmée. Recharge son état avant de la recommencer." });
    }
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
