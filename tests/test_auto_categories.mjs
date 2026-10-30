import {AUTO_CATEGORIES, categoriesAvailable, filterAutoModels, findCategoryModel} from '../static/js/modelAutoDialog.js';

function assert(condition, message) { if (!condition) throw new Error(message); }
const a = {endpointId: 'primary', mid: 'same/model', epName: 'Shared provider', display: 'Same', modelType: 'llm'};
const b = {...a, endpointId: 'alternate', epName: 'Shared provider'};
const identity = endpoint_id => ({endpoint_id, model: 'same/model'});

Deno.test('category assignment distinguishes same model IDs on different endpoints', () => {
  assert(AUTO_CATEGORIES.length === 2, 'Exactly two categories');
  assert(findCategoryModel(identity('alternate'), [a, b]) === b, 'Exact endpoint identity');
  assert(categoriesAvailable({complex_tasks: identity('primary'), straightforward_tasks: identity('alternate')}, [a, b]), 'Both explicit assignments');
  assert(!categoriesAvailable({complex_tasks: identity('primary'), straightforward_tasks: identity('alternate')}, [a]), 'Removed endpoint cannot alias another');
});
Deno.test('same model may be deliberately assigned twice but both categories are required', () => {
  assert(categoriesAvailable({complex_tasks: identity('primary'), straightforward_tasks: identity('primary')}, [a]), 'Intentional shared identity');
  assert(!categoriesAvailable({complex_tasks: identity('primary')}, [a]), 'No inferred second assignment');
  assert(!categoriesAvailable({complex_tasks: identity('primary'), straightforward_tasks: identity('primary')}, [{...a, stale: true}]), 'Unavailable cannot save');
});
Deno.test('search matches all words across full model and endpoint identity', () => {
  assert(filterAutoModels([a, b], ' SAME/model alternate ').length === 1, 'Model plus endpoint filter');
  assert(filterAutoModels([a, b], 'provider primary')[0] === a, 'Provider plus exact endpoint filter');
  assert(filterAutoModels([a, b], 'unknown').length === 0, 'No match stays empty');
  assert(filterAutoModels([{...a, mid: 'model-' + 'long-'.repeat(70)}], 'long-long').length === 1, 'Search retains full long identity');
  assert(filterAutoModels([a, {...b, modelType: 'embedding'}], '').length === 1, 'Only API chat catalogue');
});
