namespace QcmDemo;

public static class QuizRules
{
    public static bool IsCorrect(int answer, int correct) => answer == correct;
    public static int Score(bool a, bool b, bool c) => a ? (b ? (c ? 3 : 2) : (c ? 2 : 1)) : (b ? (c ? 2 : 1) : (c ? 1 : 0));
    public static bool Passed(int score) => score >= 2;
}
