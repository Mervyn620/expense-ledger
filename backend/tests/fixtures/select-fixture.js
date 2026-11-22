document.getElementById('fixtureForm').addEventListener('change', () => {
  const count = document.getElementById('changes'); count.textContent = Number(count.textContent) + 1;
});
document.getElementById('fixtureForm').addEventListener('submit', event => {
  event.preventDefault(); document.getElementById('result').textContent = new FormData(event.currentTarget).get('choice');
});
document.getElementById('replace').addEventListener('click', () => {
  document.getElementById('fields').innerHTML = '<label>动态选项<select name="choice" required><option value="x">X-ray</option><option value="y">Yankee</option></select></label>';
});
document.getElementById('disable').addEventListener('click', () => {
  const select = document.querySelector('select'); select.disabled = !select.disabled;
});
