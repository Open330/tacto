"use strict";

(() => {
  const demos = {
    replay: {
      status: "VERIFIED REPLAY",
      total: "모델 호출 0회",
      lines: [
        ["01", "새 관찰에서 입력 필드 확인", ""],
        ["02", "매개변수로 입력 · 보너스 상태 확인", ""],
        ["03", "검토 → 계산 → 완료", ""],
        ["check", "결과 조건 검증 완료", "good"],
      ],
    },
    repair: {
      status: "CHECKED CONTINUATION",
      total: "설명용 예시 · 수리 1회",
      lines: [
        ["01", "계산 버튼의 선택자 변경 감지", "warn"],
        ["02", "실패 단계와 후보만 에이전트에 전달", ""],
        ["03", "현재 상태와 수리 후보 검증", ""],
        ["check", "남은 단계부터 재개 · 앞 단계 유지", "good"],
      ],
    },
    stop: {
      status: "STOPPED · NO RESEND",
      total: "불확실한 완료는 재전송 금지",
      lines: [
        ["01", "완료 요청 후 결과 확인 실패", "warn"],
        ["02", "이미 처리됐을 가능성 확인", ""],
        ["03", "같은 요청을 다시 보내지 않음", ""],
        ["stop", "작업 중단 · 현재 상태 검토 필요", "warn"],
      ],
    },
  };

  const tabs = [...document.querySelectorAll("[data-demo]")];
  const panel = document.getElementById("demo-panel");
  const lines = document.getElementById("demo-lines");

  function renderDemo(name) {
    const demo = demos[name];
    if (!demo) return;
    lines.replaceChildren(
      ...demo.lines.map(([number, text, tone]) => {
        const row = document.createElement("div");
        const label = document.createElement("span");
        label.className = "line-number";
        if (number === "check" || number === "stop") {
          const svg = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "svg",
          );
          svg.classList.add("ui-icon");
          svg.setAttribute("aria-hidden", "true");
          svg.setAttribute("focusable", "false");
          svg.setAttribute("viewBox", "0 0 24 24");
          const use = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "use",
          );
          use.setAttribute("href", `assets/ui-icons.svg#${number}`);
          svg.append(use);
          label.append(svg);
        } else {
          label.textContent = number;
        }
        const content = document.createElement("span");
        content.className = tone;
        content.textContent = text;
        row.append(label, content);
        return row;
      }),
    );
    document.getElementById("demo-status").textContent = demo.status;
    document.getElementById("demo-total").textContent = demo.total;
    panel?.setAttribute("aria-labelledby", `demo-tab-${name}`);
    tabs.forEach((button) => {
      const selected = button.dataset.demo === name;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
  }

  tabs.forEach((button, index) => {
    button.addEventListener("click", () => renderDemo(button.dataset.demo));
    button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft")
        next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      renderDemo(tabs[next].dataset.demo);
      tabs[next].focus({ preventScroll: true });
    });
  });

  const agents = [...document.querySelectorAll("[data-agent]")];
  agents.forEach((button) =>
    button.addEventListener("click", () => {
      document.getElementById("agent-label").textContent = button.dataset.agent;
      agents.forEach((other) => {
        const selected = other === button;
        other.classList.toggle("selected", selected);
        other.setAttribute("aria-pressed", String(selected));
      });
    }),
  );

  let toastTimer;
  function toast(message) {
    const box = document.querySelector(".toast");
    box.textContent = message;
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 2600);
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {
        // The same user click can still use the LAN-compatible fallback.
      }
    }
    const previousFocus = document.activeElement;
    const selection = window.getSelection();
    const ranges = selection
      ? Array.from({ length: selection.rangeCount }, (_, i) =>
          selection.getRangeAt(i).cloneRange(),
        )
      : [];
    const area = document.createElement("textarea");
    area.value = text;
    area.className = "clipboard-fallback";
    area.setAttribute("aria-label", "복사할 명령");
    document.body.append(area);
    try {
      area.focus({ preventScroll: true });
      area.select();
      if (!document.execCommand("copy")) throw new Error("Copy unavailable");
    } finally {
      area.remove();
      previousFocus?.focus({ preventScroll: true });
      if (selection && ranges.length) {
        selection.removeAllRanges();
        ranges.forEach((range) => selection.addRange(range));
      }
    }
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      try {
        await copyText(target.innerText.trim());
        toast("복사했습니다. 터미널 또는 에이전트에 붙여넣으세요.");
      } catch {
        toast("자동 복사가 불가능합니다. 명령을 직접 선택해 복사하세요.");
      } finally {
        button.focus({ preventScroll: true });
      }
    });
  });

  // Keep an unloaded video on its poster until the user requests playback.
  // Native controls remain the no-JavaScript fallback and take over on play.
  document.querySelectorAll("video").forEach((video) => {
    const shell = document.createElement("div");
    shell.className = "video-shell";
    video.before(shell);
    shell.append(video);
    const start = document.createElement("button");
    start.type = "button";
    start.className = "video-start";
    start.setAttribute(
      "aria-label",
      `영상 재생: ${video.getAttribute("aria-label") || "실행 예제"}`,
    );
    start.innerHTML =
      '<svg class="ui-icon" aria-hidden="true" viewBox="0 0 24 24"><use href="assets/ui-icons.svg#play"></use></svg><span>영상 재생</span>';
    shell.append(start);
    video.controls = false;
    video.addEventListener("play", () => {
      video.controls = true;
      start.hidden = true;
    });
    start.addEventListener("click", async () => {
      video.controls = true;
      start.hidden = true;
      try {
        await video.play();
      } catch {
        // Leave the native controls available when playback needs another click.
        video.controls = true;
      }
    });
  });

  // Workflow designs are separate from the evidence clips, which only verify
  // individual actions. Switching scenarios never starts video playback.
  const carousel = document.getElementById("task-carousel");
  if (carousel) {
    const workflows = {
      triage: {
        title: "다음 회의 전에, 검토할 이슈만 모아두세요.",
        request: "“이번 주 열린 버그를 찾아 이전 목록과 비교하고, 새로 확인할 이슈를 정리해줘.”",
        steps: [["GitHub", "대상 저장소에서 열린 버그 검색"], ["이전 검토 목록", "이미 검토한 항목과 새 항목 대조"], ["검토용 목록", "이슈 링크·댓글 수·확인할 점 정리"]],
        result: "다음 회의에서 바로 열어볼 이슈 목록. 우선순위와 담당자는 사람이 결정합니다.",
        repeat: "저장소와 조회 조건을 바꿔 같은 점검 절차를 재사용하는 설계입니다.",
        evidence: "github", kind: "실제 웹사이트 · GitHub", label: "실제 녹화: 검색·필터·정렬 단계",
        scope: "열린 버그를 댓글순으로 정렬하고 결과 25개를 확인했습니다. 이전 목록 대조·검토 목록 생성은 이 영상에 포함되지 않습니다.",
      },
      expenses: {
        title: "월말마다, 증빙을 찾는 일부터 반복하지 않도록.",
        request: "“이번 달 증빙 파일을 모아 정산표와 대조하고, 빠진 항목과 중복 후보를 표시해줘.”",
        steps: [["브라우저 · 다운로드", "지정한 서비스에서 증빙 파일 수집"], ["Finder · 스프레드시트", "날짜·거래처·금액을 정산표와 대조"], ["검토용 폴더", "누락·중복 후보와 원본 파일 연결"]],
        result: "원본을 찾아볼 수 있는 정산 검토표. 불분명한 금액·중복 후보는 검토 대상으로 남깁니다.",
        repeat: "대상 월과 증빙 폴더를 바꿔 재사용하는 설계입니다. 자동 제출은 포함하지 않습니다.",
        evidence: "finder", kind: "실제 Mac 앱 · Finder", label: "실제 녹화: 파일 목록 정렬 단계",
        scope: "촬영용 폴더의 파일 5개를 종류·이름으로 정렬했습니다. 증빙 다운로드·정보 추출·정산표 대조는 아직 검증하지 않았습니다.",
      },
      support: {
        title: "문의 내용과 주문 화면을 오가는 시간을 줄이세요.",
        request: "“배송 문의를 분류하고 주문 상태를 확인한 다음, 답변 초안을 검토할 수 있게 준비해줘.”",
        steps: [["문의함", "배송·교환 등 요청 내용 분류"], ["주문 관리 화면", "주문 번호와 현재 처리 상태 대조"], ["답변 초안", "고객 언어로 답변 준비 · 전송 전 검토"]],
        result: "주문 근거가 연결된 답변 초안. 주문이 일치하지 않거나 정책 판단이 필요하면 검토 대상으로 남깁니다.",
        repeat: "새 문의 묶음에 같은 확인 절차를 적용하는 설계입니다. 고객에게 자동 전송하는 기능을 시연한 것은 아닙니다.",
        evidence: "translate", kind: "실제 웹사이트 · Google Translate", label: "실제 녹화: 번역 입력·결과 확인 단계",
        scope: "영어 예문 입력 뒤 한국어 결과를 확인했습니다. 전송 단계의 완료 증명은 미확정이며, 문의함·주문 조회·답장 저장은 검증하지 않았습니다.",
      },
      backoffice: {
        title: "여러 행을 옮겨 적는 일에도, 확인 절차를 함께.",
        request: "“검토된 CSV의 항목을 관리자 화면에 입력하고, 저장된 값이 원본과 같은지 확인해줘.”",
        steps: [["CSV · 스프레드시트", "필수 값·중복 항목을 먼저 확인"], ["관리자 화면", "각 행의 값을 대응하는 입력란에 등록"], ["저장 결과", "원본과 대조 · 실패한 행은 별도 표시"]],
        result: "행별 처리 결과와 확인이 필요한 항목. 저장 여부가 불분명한 행은 재등록 전에 상태부터 검토합니다.",
        repeat: "입력 파일을 바꿔 같은 필드 매핑을 재사용하는 설계입니다. 실제 서비스별 연동 검증은 별도로 필요합니다.",
        evidence: "form", kind: "공개 테스트 사이트 · Selenium", label: "테스트 녹화: 입력란·선택 항목 조작",
        scope: "공개 테스트 폼의 입력·선택 조작만 촬영했습니다. 제출하지 않았으며 실제 관리자 서비스·CSV 연계·일괄 저장 완료를 증명하지 않습니다.",
      },
    };
    const video = carousel.querySelector("video");
    const start = carousel.querySelector(".video-start");
    const choices = [...carousel.querySelectorAll("[data-workflow]")];
    const rotation = document.getElementById("carousel-rotation");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const wideScreen = window.matchMedia("(min-width: 901px)");
    let index = 0, rotating = !motion.matches && wideScreen.matches, hovering = false, timer;
    const updateRotation = () => {
      rotation.textContent = rotating ? "자동 넘김 멈추기" : "자동 넘김 시작하기";
      clearTimeout(timer);
      if (rotating && !hovering && !document.hidden && video.paused &&
          (!carousel.contains(document.activeElement) || document.activeElement === rotation)) {
        timer = setTimeout(() => show(index + 1), 12000);
      }
    };
    function show(next) {
      index = (next + choices.length) % choices.length;
      const item = workflows[choices[index].dataset.workflow];
      const recording = document.querySelector(`#example-${item.evidence} video`);
      video.pause();
      video.controls = false;
      video.poster = recording.poster;
      video.setAttribute("aria-label", recording.getAttribute("aria-label"));
      video.querySelector("source").src = recording.querySelector("source").src;
      video.querySelector("track").src = recording.querySelector("track").src;
      video.querySelector("a").href = recording.querySelector("source").src;
      video.src = recording.querySelector("source").src;
      start.hidden = false;
      start.setAttribute("aria-label", `영상 재생: ${recording.getAttribute("aria-label")}`);
      for (const [id, value] of Object.entries({
        "workflow-title": item.title, "workflow-request": item.request,
        "workflow-result": item.result, "workflow-repeat": item.repeat,
        "carousel-evidence-kind": item.kind, "carousel-title": item.label,
        "carousel-description": item.scope,
      })) document.getElementById(id).textContent = value;
      document.getElementById("workflow-steps").replaceChildren(...item.steps.map(([app, task]) => {
        const row = document.createElement("li");
        const label = document.createElement("span");
        const text = document.createElement("strong");
        label.textContent = app; text.textContent = task; row.append(label, text);
        return row;
      }));
      document.getElementById("carousel-detail").href = `#example-${item.evidence}`;
      document.getElementById("carousel-count").textContent = `${index + 1} / ${choices.length}`;
      choices.forEach((choice, i) => choice.setAttribute("aria-pressed", String(i === index)));
      updateRotation();
    }
    choices.forEach((choice, i) => {
      choice.disabled = false;
      choice.addEventListener("click", () => { rotating = false; show(i); });
    });
    document.getElementById("carousel-prev").addEventListener("click", () => { rotating = false; show(index - 1); });
    document.getElementById("carousel-next").addEventListener("click", () => { rotating = false; show(index + 1); });
    rotation.addEventListener("click", () => { rotating = !rotating; updateRotation(); });
    carousel.addEventListener("mouseenter", () => { hovering = true; updateRotation(); });
    carousel.addEventListener("mouseleave", () => { hovering = false; updateRotation(); });
    carousel.addEventListener("focusin", (event) => {
      if (event.target === rotation) clearTimeout(timer);
      else { rotating = false; updateRotation(); }
    });
    carousel.addEventListener("focusout", () => setTimeout(updateRotation, 0));
    video.addEventListener("play", () => { rotating = false; updateRotation(); });
    document.addEventListener("visibilitychange", updateRotation);
    motion.addEventListener("change", () => { if (motion.matches) rotating = false; updateRotation(); });
    wideScreen.addEventListener("change", () => { if (!wideScreen.matches) rotating = false; updateRotation(); });
    carousel.querySelector(".carousel-toolbar").hidden = false;
    carousel.querySelector(".carousel-navigation").hidden = false;
    updateRotation();
  }

  const filters = document.querySelector(".example-filters");
  const cases = [...document.querySelectorAll(".everyday-case")];
  if (filters && cases.length) {
    filters.hidden = false;
    filters.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        const kind = button.dataset.exampleFilter;
        filters
          .querySelectorAll("button")
          .forEach((item) =>
            item.setAttribute("aria-pressed", String(item === button)),
          );
        cases.forEach((card) => {
          card.hidden = kind !== "all" && card.dataset.category !== kind;
          if (card.hidden) card.querySelector("video")?.pause();
        });
        document.querySelector("#example-count").textContent =
          `${cases.filter((card) => !card.hidden).length}개 사례를 표시합니다.`;
      });
    });
  }

  const flowCases = {
    first: {left: "7", right: "8", result: "15"},
    second: {left: "3", right: "9", result: "12"},
  };
  const flowWalk = document.getElementById("flow-walk");
  if (flowWalk) {
    const choices = [...document.querySelectorAll("[data-flow-case]")];
    const nodes = [...document.querySelectorAll("[data-flow-node]")];
    const status = document.getElementById("flow-preview-status");
    const canvas = document.getElementById("flow-canvas");
    const edgeGroup = document.getElementById("flow-edge-paths");
    function drawEdges() {
      const bounds = canvas.getBoundingClientRect();
      canvas.querySelector(".flow-edges").setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
      const point = (id, side) => {
        const r = document.getElementById(id).getBoundingClientRect();
        return { x: r.left - bounds.left + (side === "left" ? 0 : side === "right" ? r.width : r.width / 2),
          y: r.top - bounds.top + (side === "top" ? 0 : side === "bottom" ? r.height : r.height / 2) };
      };
      const edges = [];
      function connect(from, to, kind, sides) {
        const a = document.getElementById(from).getBoundingClientRect();
        const b = document.getElementById(to).getBoundingClientRect();
        const horizontal = Math.abs(a.top - b.top) < 12;
        const ports = sides || (horizontal ? (a.left < b.left ? ["right", "left"] : ["left", "right"]) : ["bottom", "top"]);
        const narrow = window.matchMedia("(max-width: 540px)").matches;
        const detour = kind === "exception" && window.matchMedia("(max-width: 1000px)").matches;
        if (detour || (narrow && from === "graph-saved")) {
          const side = from === "graph-step-5" && narrow ? "right" : "left";
          const p = point(from, side), q = point(to, side);
          const x = side === "left" ? 8 : bounds.width - 8;
          edges.push(`<path class="flow-edge ${kind}" d="M ${p.x} ${p.y} H ${x} V ${q.y} H ${q.x}"/><circle class="flow-connection ${kind}" cx="${p.x}" cy="${p.y}" r="3.5"/><circle class="flow-connection ${kind}" cx="${q.x}" cy="${q.y}" r="3.5"/>`);
          return;
        }
        const p = point(from, ports[0]), q = point(to, ports[1]);
        const vertical = ["top", "bottom"].includes(ports[0]);
        const d = vertical ? `M ${p.x} ${p.y} C ${p.x} ${(p.y+q.y)/2}, ${q.x} ${(p.y+q.y)/2}, ${q.x} ${q.y}`
          : `M ${p.x} ${p.y} C ${(p.x+q.x)/2} ${p.y}, ${(p.x+q.x)/2} ${q.y}, ${q.x} ${q.y}`;
        edges.push(`<path class="flow-edge ${kind}" d="${d}"/><circle class="flow-connection ${kind}" cx="${p.x}" cy="${p.y}" r="3.5"/><circle class="flow-connection ${kind}" cx="${q.x}" cy="${q.y}" r="3.5"/>`);
      }
      connect("graph-agent", "graph-saved", "authoring");
      connect("graph-saved", "graph-step-0", "authoring", ["bottom", "top"]);
      for (let i = 0; i < 5; i++) connect(`graph-step-${i}`, `graph-step-${i+1}`, "replay");
      connect("graph-step-3", "graph-repair", "exception", ["bottom", "top"]);
      connect("graph-step-5", "graph-stop", "exception", ["bottom", "top"]);
      edgeGroup.innerHTML = edges.join("");
    }
    new ResizeObserver(drawEdges).observe(canvas);
    document.fonts.ready.then(drawEdges);
    drawEdges();
    let selected = "first", timer;
    const stop = () => { clearTimeout(timer); flowWalk.disabled = false; };
    function renderFlow(name) {
      stop(); selected = name;
      const item = flowCases[name];
      document.getElementById("flow-left").textContent = item.left;
      document.getElementById("flow-right").textContent = item.right;
      document.getElementById("flow-result").textContent = item.result;
      nodes.forEach(node => node.removeAttribute("data-state"));
      status.textContent = `녹화에서 확인한 결과: ${item.left} + ${item.right} = ${item.result}`;
      choices.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.flowCase === name)));
    }
    choices.forEach(button => {
      button.disabled = false;
      button.addEventListener("click", () => renderFlow(button.dataset.flowCase));
    });
    flowWalk.disabled = false;
    flowWalk.addEventListener("click", () => {
      renderFlow(selected); flowWalk.disabled = true;
      function step(index) {
        nodes.forEach((node, i) => {
          if (i < index) node.dataset.state = "done";
          else if (i === index) node.dataset.state = "active";
          else node.removeAttribute("data-state");
        });
        if (index === nodes.length) {
          const item = flowCases[selected];
          status.textContent = `녹화에서 확인한 결과: ${item.left} + ${item.right} = ${item.result}`;
          flowWalk.disabled = false; return;
        }
        status.textContent = `단계 ${index + 1} / ${nodes.length} · ${nodes[index].querySelector("strong").textContent}`;
        timer = setTimeout(() => step(index + 1), 550);
      }
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) step(nodes.length);
      else step(0);
    });
    document.addEventListener("visibilitychange", () => { if (document.hidden) renderFlow(selected); });
  }

  renderDemo("replay");
  document
    .querySelectorAll(
      "button[data-demo], button[data-agent], button[data-copy]",
    )
    .forEach((button) => {
      button.disabled = false;
    });
})();
