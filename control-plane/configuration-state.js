(function (root) {
  function copyEntries(entries) {
    return Array.isArray(entries) ? entries.map(entry => ({...entry})) : [];
  }

  function snapshot(config, sources, filingClasses) {
    return {
      ...config,
      sources: copyEntries(sources),
      filing_classes: copyEntries(filingClasses),
    };
  }

  function add(config, collection, entry) {
    return {...config, [collection]: [...copyEntries(config[collection]), {...entry}]};
  }

  function remove(config, collection, index) {
    const entries = copyEntries(config[collection]);
    if (Number.isInteger(index) && index >= 0 && index < entries.length) entries.splice(index, 1);
    return {...config, [collection]: entries};
  }

  function rowLabel(prefix, index) {
    return `${prefix} ${index + 1}`;
  }

  const api = {add, remove, rowLabel, snapshot};
  root.AriadneCleanupState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : (typeof module !== "undefined" ? module.exports : {}));
