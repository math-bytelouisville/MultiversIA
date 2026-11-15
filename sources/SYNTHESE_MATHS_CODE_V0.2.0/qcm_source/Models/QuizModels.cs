namespace QcmDemo.Models;

public sealed record QuestionData(string Id, string Text, string[] Options, int Correct);
public sealed record QuizData(string Title, QuestionData[] Questions);
public sealed record PublicQuestion(string Id, string Text, string[] Options);
public sealed record PublicQuiz(string Title, PublicQuestion[] Questions);
public sealed record GradeResult(int Score, int Total, bool Passed, bool[] Correct, int[] Expected);
