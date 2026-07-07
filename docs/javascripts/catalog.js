(function () {
  // === Shared Utilities ===

  function escapeText(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.textContent;
  }

  function createTag(text, className) {
    var span = document.createElement("span");
    span.className = "tag " + className;
    span.textContent = text;
    return span;
  }

  function sanitizeHTML(html) {
    return DOMPurify.sanitize(html, { ALLOWED_TAGS: ["a", "strong"], ALLOWED_ATTR: ["href", "target", "rel"] });
  }

  function fixRepoLink() {
    var source = document.querySelector(".md-source");
    if (source) {
      source.setAttribute("target", "_blank");
      source.setAttribute("rel", "noopener");
    }
    // Make all external links open in a new tab
    var siteHost = window.location.hostname;
    document.querySelectorAll("a[href]").forEach(function (link) {
      var href = link.getAttribute("href");
      if (href && href.startsWith("http")) {
        try {
          var linkHost = new URL(href).hostname;
          if (linkHost !== siteHost) {
            link.setAttribute("target", "_blank");
            link.setAttribute("rel", "noopener");
          }
        } catch (e) {}
      }
    });
  }

  // === Skills Catalog ===

  function initSkillCatalog() {
    var container = document.getElementById("skills-catalog-root");
    if (!container) return;

    var dataUrl = container.getAttribute("data-source");
    if (!dataUrl) return;

    fetch(dataUrl)
      .then(function (r) { return r.json(); })
      .then(function (skills) { renderSkills(container, skills); })
      .catch(function (err) { console.error("Skills catalog:", err); });
  }

  function createSkillCard(skill) {
    var card = document.createElement("div");
    card.className = "skill-card";

    // Set data attributes for filtering
    Object.keys(skill.dimensions).forEach(function (dimKey) {
      card.setAttribute("data-dim-" + dimKey, skill.dimensions[dimKey].join(","));
    });

    // Title with link
    var h3 = document.createElement("h3");
    var link = document.createElement("a");
    link.href = escapeText(skill.id) + "/";
    link.textContent = skill.name;
    h3.appendChild(link);
    card.appendChild(h3);

    // Description (may contain safe HTML links from the hook)
    var desc = document.createElement("p");
    desc.innerHTML = sanitizeHTML(skill.description);
    card.appendChild(desc);

    // Author
    var meta = document.createElement("div");
    meta.className = "skill-meta";
    var author = document.createElement("span");
    author.className = "skill-author";
    var authorLink = document.createElement("a");
    authorLink.href = "https://github.com/" + encodeURIComponent(skill.author);
    authorLink.target = "_blank";
    authorLink.rel = "noopener";
    authorLink.textContent = skill.author;
    author.textContent = "by ";
    author.appendChild(authorLink);
    meta.appendChild(author);
    card.appendChild(meta);

    // Tags
    var tagsDiv = document.createElement("div");
    tagsDiv.className = "skill-tags";
    Object.keys(skill.dimensions).forEach(function (dimKey) {
      var tagClass = "tag-agent";
      if (dimKey === "aws-services") tagClass = "tag-service";
      else if (dimKey === "technical-domains") tagClass = "tag-domain";
      skill.dimensions[dimKey].forEach(function (val) {
        tagsDiv.appendChild(createTag(val, tagClass));
      });
    });
    card.appendChild(tagsDiv);

    return card;
  }

  function renderSkills(container, skills) {
    // Collect all unique dimension keys
    var dimKeys = [];
    var dimLabels = {};
    skills.forEach(function (skill) {
      Object.keys(skill.dimensions).forEach(function (key) {
        if (dimKeys.indexOf(key) === -1) {
          dimKeys.push(key);
          dimLabels[key] = formatDimLabel(key);
        }
      });
    });
    dimKeys.sort();

    // Clear container safely
    container.textContent = "";

    // Build filter buttons using DOM APIs
    var filtersEl = document.createElement("div");
    filtersEl.id = "skill-filters";
    var filterGroup = document.createElement("div");
    filterGroup.className = "filter-group";

    var label = document.createElement("span");
    label.className = "filter-label";
    label.textContent = "Group by:";
    filterGroup.appendChild(label);

    var allBtn = document.createElement("button");
    allBtn.className = "filter-btn active";
    allBtn.setAttribute("data-group", "none");
    allBtn.textContent = "All";
    filterGroup.appendChild(allBtn);

    dimKeys.forEach(function (key) {
      var btn = document.createElement("button");
      btn.className = "filter-btn";
      btn.setAttribute("data-group", key);
      btn.textContent = dimLabels[key];
      filterGroup.appendChild(btn);
    });

    filtersEl.appendChild(filterGroup);
    container.appendChild(filtersEl);

    // Build catalog grid
    var catalogEl = document.createElement("div");
    catalogEl.id = "skill-catalog";
    var cards = [];
    skills.forEach(function (skill) {
      var card = createSkillCard(skill);
      cards.push(card);
      catalogEl.appendChild(card);
    });
    container.appendChild(catalogEl);

    // Grouped view container
    var groupedEl = document.createElement("div");
    groupedEl.id = "skill-grouped";
    groupedEl.style.display = "none";
    container.appendChild(groupedEl);

    // Attach filter logic via event delegation
    filtersEl.addEventListener("click", function (e) {
      var btn = e.target.closest(".filter-btn");
      if (!btn) return;

      filtersEl.querySelectorAll(".filter-btn").forEach(function (b) {
        b.classList.remove("active");
      });
      btn.classList.add("active");

      var groupBy = btn.getAttribute("data-group");

      if (groupBy === "none") {
        catalogEl.style.display = "";
        groupedEl.style.display = "none";
        groupedEl.textContent = "";
        return;
      }

      // Build groups
      var groups = {};
      cards.forEach(function (card) {
        var attr = card.getAttribute("data-dim-" + groupBy);
        if (!attr) return;
        attr.split(",").forEach(function (val) {
          val = val.trim();
          if (!groups[val]) groups[val] = [];
          groups[val].push(card);
        });
      });

      // Render grouped view using DOM APIs
      groupedEl.textContent = "";
      Object.keys(groups).sort().forEach(function (key) {
        var heading = document.createElement("h3");
        heading.className = "group-heading";
        heading.textContent = key;
        groupedEl.appendChild(heading);

        var groupDiv = document.createElement("div");
        groupDiv.className = "skill-group";
        groups[key].forEach(function (card) {
          groupDiv.appendChild(card.cloneNode(true));
        });
        groupedEl.appendChild(groupDiv);
      });

      catalogEl.style.display = "none";
      groupedEl.style.display = "";
    });
  }

  function formatDimLabel(key) {
    return key
      .replace(/-/g, " ")
      .replace(/\b(aws|eks|rds|rca|mcp)\b/gi, function (m) { return m.toUpperCase(); })
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); })
      .replace(/s$/, "");
  }

  // === Agents Catalog ===

  function initAgentsCatalog() {
    var container = document.getElementById("agents-catalog-root");
    if (!container) return;

    var dataUrl = container.getAttribute("data-source");
    if (!dataUrl) return;

    fetch(dataUrl)
      .then(function (r) { return r.json(); })
      .then(function (agents) { renderAgents(container, agents); })
      .catch(function (err) { console.error("Agents catalog:", err); });
  }

  function createAgentCard(agent) {
    var card = document.createElement("div");
    card.className = "skill-card";

    // Title with link
    var h3 = document.createElement("h3");
    var link = document.createElement("a");
    link.href = escapeText(agent.id) + "/";
    link.textContent = agent.name;
    h3.appendChild(link);
    card.appendChild(h3);

    // Description
    var desc = document.createElement("p");
    desc.innerHTML = sanitizeHTML(agent.description);
    card.appendChild(desc);

    // Tags (tools + skills)
    var tagsDiv = document.createElement("div");
    tagsDiv.className = "skill-tags";
    (agent.tools || []).forEach(function (tool) {
      tagsDiv.appendChild(createTag(tool, "tag-tool"));
    });
    (agent.skills || []).forEach(function (skill) {
      tagsDiv.appendChild(createTag(skill, "tag-skill"));
    });
    card.appendChild(tagsDiv);

    return card;
  }

  function renderAgents(container, agents) {
    container.textContent = "";

    var catalogEl = document.createElement("div");
    catalogEl.id = "agents-catalog";
    agents.forEach(function (agent) {
      catalogEl.appendChild(createAgentCard(agent));
    });
    container.appendChild(catalogEl);
  }

  // === Initialize ===

  function initAll() {
    initSkillCatalog();
    initAgentsCatalog();
    fixRepoLink();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Material instant navigation
  if (typeof document$ !== "undefined") {
    document$.subscribe(initAll);
  }
})();
