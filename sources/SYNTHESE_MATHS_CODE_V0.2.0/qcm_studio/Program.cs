using System.Net;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Mvc;
using QcmStudio.Services;

var builder = WebApplication.CreateBuilder(new WebApplicationOptions {
    Args = [], ContentRootPath = AppContext.BaseDirectory
});
builder.Logging.ClearProviders();
builder.WebHost.ConfigureKestrel(options => { options.Listen(IPAddress.Loopback, 0); options.Limits.MaxRequestBodySize = 1024 * 1024; });
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
    if (context.Request.Path.StartsWithSegments("/api")) context.Response.Headers.CacheControl = "no-store";
    try {
        if (context.Request.Host.Host is not ("127.0.0.1" or "localhost")) throw new StudioException(400, "LOCAL_HOST_REQUIRED", "Ce studio est accessible uniquement sur l’hôte local.");
        if (!HttpMethods.IsGet(context.Request.Method) && !HttpMethods.IsHead(context.Request.Method)) {
            var origin = context.Request.Headers.Origin.ToString();
            var own = context.Request.Scheme + "://" + context.Request.Host.Value;
            if ((origin.Length > 0 && !string.Equals(origin, own, StringComparison.OrdinalIgnoreCase)) || context.Request.Headers["Sec-Fetch-Site"] == "cross-site")
                throw new StudioException(403, "SAME_ORIGIN_REQUIRED", "Les modifications doivent venir du studio ouvert sur cette même adresse locale.");
            if (!string.Equals(context.Request.ContentType?.Split(';')[0].Trim(), "application/json", StringComparison.OrdinalIgnoreCase))
                throw new StudioException(400, "JSON_REQUIRED", "Les modifications utilisent uniquement un objet JSON.");
        }
        await next(context);
    } catch (StudioException ex) {
        context.Response.StatusCode = ex.Status;
        await context.Response.WriteAsJsonAsync(new { error = ex.Code, message = ex.Message });
    } catch (Exception ex) {
        Console.Error.WriteLine(ex.ToString());
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
