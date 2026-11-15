using System.Numerics;
using QcmStudio.Models;

namespace QcmStudio.Services;

public sealed class StudioException(int status, string code, string message) : Exception(message) {
    public int Status { get; } = status;
    public string Code { get; } = code;
}
public interface IPlayerProvider {
    Player Current(StudioState state);
    void Rename(StudioState state, string displayName);
}
public sealed class LocalPlayerProvider : IPlayerProvider {
    public Player Current(StudioState state) => new() { Id = state.Account.Id, DisplayName = state.Account.DisplayName };
    public void Rename(StudioState state, string displayName) { state.Account.DisplayName = displayName; }
}
// Registry is an actual DI extension point; other game APIs are not implemented.
public interface IGameDefinition { string Id { get; } string Title { get; } bool Enabled { get; } }
public sealed class QcmGameDefinition : IGameDefinition {
    public string Id => "qcm"; public string Title => "QCM"; public bool Enabled => true;
}
public sealed class GameCatalog(IEnumerable<IGameDefinition> definitions) {
    private readonly IGameDefinition[] games = definitions.ToArray();
    public object[] Entries() => games.Select(g => (object)new { g.Id, g.Title, g.Enabled }).ToArray();
}

// Exact rational points and thresholds. Only the final presentation is rounded.
public readonly struct Fraction : IComparable<Fraction> {
    public BigInteger N { get; }
    public BigInteger D { get; }
    public Fraction(BigInteger numerator, BigInteger denominator) {
        if (denominator <= 0) throw new ArgumentOutOfRangeException(nameof(denominator));
        var gcd = BigInteger.GreatestCommonDivisor(BigInteger.Abs(numerator), denominator);
        N = numerator / gcd; D = denominator / gcd;
    }
    public static Fraction Zero => new(0, 1);
    public static Fraction One => new(1, 1);
    public static Fraction operator +(Fraction a, Fraction b) => new(a.N * b.D + b.N * a.D, a.D * b.D);
    public static Fraction operator -(Fraction a, Fraction b) => new(a.N * b.D - b.N * a.D, a.D * b.D);
    public int CompareTo(Fraction other) => (N * other.D).CompareTo(other.N * D);
    public Fraction Times(int numerator, int denominator = 1) => new(N * numerator, D * denominator);
    public decimal Rounded() {
        var cents = BigInteger.DivRem(N * 100, D, out var remainder);
        if (remainder * 2 >= D) cents++;
        return (decimal)cents / 100;
    }
    public static Fraction OfDecimal(decimal value) {
        var bits = decimal.GetBits(value);
        var n = (BigInteger)(uint)bits[0] + ((BigInteger)(uint)bits[1] << 32) + ((BigInteger)(uint)bits[2] << 64);
        if (bits[3] < 0) n = -n;
        return new(n, BigInteger.Pow(10, (bits[3] >> 16) & 255));
    }
}
