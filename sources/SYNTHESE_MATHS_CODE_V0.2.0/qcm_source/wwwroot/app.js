'use strict';
(() => {
  const form = document.getElementById('quiz-form');
  const submit = document.getElementById('submit-answers');
  const result = document.getElementById('result');
  const error = document.getElementById('error-message');
  const progress = document.getElementById('progress');
  const keys = ['a', 'b', 'c'];
  let pending = false;
  function clearResult() {
    result.hidden = true;
    document.querySelectorAll('.feedback').forEach(node => { node.textContent = ''; });
    document.querySelectorAll('.question').forEach(node => { node.classList.remove('is-correct', 'is-incorrect'); });
  }
  function updateProgress() {
    const count = keys.filter(key => form.querySelector(`input[name="${key}"]:checked`)).length;
    progress.textContent = `${count} sur 3 réponses choisies`;
  }
  form.addEventListener('change', () => { clearResult(); error.hidden = true; updateProgress(); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending || !form.reportValidity()) return;
    const query = new URLSearchParams(new FormData(form));
    pending = true; submit.disabled = true; error.hidden = true; clearResult();
    form.querySelectorAll('input').forEach(input => { input.disabled = true; });
    submit.textContent = 'Correction en cours…';
    try {
      const response = await fetch('/api/quiz/grade?' + query.toString(), { cache: 'no-store' });
      if (!response.ok) throw new Error('La correction n’a pas abouti. Tes choix sont conservés ; réessaie.');
      const grade = await response.json();
      if (!Number.isInteger(grade.score) || grade.score < 0 || grade.score > 3 || grade.total !== 3 || typeof grade.passed !== 'boolean' || !Array.isArray(grade.correct) || grade.correct.length !== 3 || !grade.correct.every(value => typeof value === 'boolean') || !Array.isArray(grade.expected) || grade.expected.length !== 3 || !grade.expected.every(value => Number.isInteger(value) && value >= 0 && value < 4)) throw new Error('Le serveur a renvoyé une correction incomplète. Aucun résultat affiché.');
      document.getElementById('result-title').textContent = `${grade.score} / ${grade.total} · ${grade.passed ? 'Défi réussi !' : 'Encore un peu de pratique'}`;
      document.getElementById('result-copy').textContent = grade.passed ? 'Bien joué. Tu peux relire la correction ou tenter une nouvelle fois.' : 'Chaque essai permet de progresser. Regarde les bonnes réponses, puis recommence.';
      grade.correct.forEach((correct, index) => {
        const question = document.querySelector(`[data-question="${index}"]`);
        const option = form.querySelector(`input[name="${keys[index]}"][value="${grade.expected[index]}"]`).closest('label');
        question.classList.add(correct ? 'is-correct' : 'is-incorrect');
        document.getElementById(`feedback-${index}`).textContent = correct ? '✓ Bonne réponse' : `À revoir — bonne réponse : ${option.lastElementChild.textContent}`;
      });
      result.hidden = false; result.focus();
    } catch (failure) {
      error.textContent = failure instanceof TypeError ? 'Le serveur est injoignable. Tes réponses sont conservées ; réessaie quand il sera disponible.' : failure.message;
      error.hidden = false;
    } finally {
      pending = false; submit.disabled = false; submit.textContent = 'Corriger mes réponses →';
      form.querySelectorAll('input').forEach(input => { input.disabled = false; });
    }
  });
  document.getElementById('restart').addEventListener('click', () => {
    if (pending) return;
    form.reset(); clearResult(); error.hidden = true; updateProgress(); form.querySelector('input').focus();
  });
  fetch('/api/meta', { cache: 'no-store' }).then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  }).then(meta => {
    if (meta.profile !== 'qcm-mvc-1' || typeof meta.language !== 'string' || typeof meta.framework !== 'string') throw new Error();
    document.getElementById('server-status').textContent = `${meta.language} · ${meta.framework}`;
  }).catch(() => { document.getElementById('server-status').textContent = 'Serveur non confirmé'; });
})();
