import { session } from "./session.js";
import { ws } from "./ws.js";
import {
  $,
  el,
  toast,
  toolbar,
  showModal,
  moneyNormalize,
  formatRub,
  downloadBase64,
  dateIso,
  dateDisplay,
  monthAgoIso,
} from "./utils.js";

const appRoot = document.getElementById("app");
let connected = false;
let route = { name: "login", params: {} };
let unsub = null;

function navigate(name, params = {}) {
  route = { name, params };
  render();
}

function requireAuth() {
  if (!session.user) {
    navigate("login");
    return false;
  }
  return true;
}

function screenShell(title, bodyNodes, { back, footer } = {}) {
  const screen = el("div", { class: "screen" }, [
    toolbar({
      title,
      back: back || null,
      connected,
    }),
    el("div", { class: "content" }, bodyNodes),
    footer ? el("div", { class: "footer-actions" }, footer) : null,
  ]);
  return screen;
}

/* ---------- LOGIN ---------- */
function renderLogin() {
  const loginInput = el("input", { type: "text", value: session.login, autocomplete: "username" });
  const passInput = el("input", { type: "password", value: "", autocomplete: "current-password" });
  return screenShell("Вход", [
    el("div", { class: "login-wrap" }, [
      el("div", { class: "field" }, [el("label", { text: "Логин" }), loginInput]),
      el("div", { class: "field" }, [el("label", { text: "Пароль" }), passInput]),
      el("button", {
        class: "btn",
        type: "button",
        text: "Войти",
        onClick: async () => {
          try {
            await ws.login(loginInput.value.trim(), passInput.value);
            toast("Вход выполнен");
            navigate("main");
          } catch (e) {
            toast(e.message || "Ошибка входа");
          }
        },
      }),
    ]),
  ]);
}

/* ---------- MAIN ---------- */
function renderMain() {
  if (!requireAuth()) return el("div");
  const buttons = [];
  if (!session.isAccountant) {
    buttons.push(menuBtn("НАЧАТЬ СМЕНУ", () => navigate("shift", {})));
    buttons.push(menuBtn("АРХИВ СМЕН", () => navigate("archive")));
  }
  if (session.canAccessAccounting) {
    buttons.push(menuBtn("ДЕНЕЖНЫЕ ОПЕРАЦИИ", () => navigate("accountantMoney")));
    buttons.push(menuBtn("ЗАДОЛЖЕННОСТИ", () => navigate("debts")));
  }
  if (session.isSuperuser) {
    buttons.push(menuBtn("ОТЧЕТЫ", () => navigate("reports")));
    buttons.push(menuBtn("АДМИНИСТРИРОВАНИЕ", () => navigate("admin")));
  }
  buttons.push(
    menuBtn("ВЫЙТИ", () => {
      session.clearAuth();
      ws.authenticated = false;
      toast("Вы вышли");
      navigate("login");
    })
  );

  return el("div", { class: "screen" }, [
    el("div", { class: "toolbar" }, [
      el("div", { class: `status-dot${connected ? " on" : ""}` }),
      el("h1", { class: "toolbar-title", text: "CARTA" }),
    ]),
    el("div", { class: "hero" }, [el("div", { class: "logo-box", text: "CARTA" })]),
    el("div", { class: "menu" }, buttons),
  ]);
}

function menuBtn(text, onClick) {
  return el("button", { class: "btn", type: "button", text, onClick });
}

/* ---------- SETTINGS removed: WS always uses same host as the page ---------- */

/* ---------- SHIFT ---------- */
async function renderShift() {
  if (!requireAuth()) return el("div");
  const wrap = screenShell("Смена", [el("div", { text: "Загрузка…" })], {
    back: () => navigate("main"),
  });
  queueMicrotask(async () => {
    try {
      const shiftId = route.params.shiftId;
      const data = shiftId
        ? await ws.request("get_shift", { shift_id: Number(shiftId) })
        : await ws.request("get_current_shift");
      route.params.shiftId = data.id;
      appRoot.replaceChildren(buildShiftScreen(data));
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });
  return wrap;
}

function buildShiftScreen(data) {
  const attendances = data.attendances || [];
  const operations = data.operations || [];
  const cashStart = data.cash_start_set
    ? el("span", { text: formatRub(data.cash_start) })
    : el("span", { class: "cross", text: "✕" });
  const cashEnd = data.cash_end_set
    ? el("span", {
        class: data.cash_mismatch ? "cash-bad" : "",
        text: formatRub(data.cash_end),
      })
    : el("span", { class: "cross", text: "✕" });

  return el("div", { class: "screen" }, [
    toolbar({
      title: data.title || "Смена",
      back: () => navigate("main"),
      connected,
    }),
    el("div", { class: "content" }, [
      el("div", { class: "row" }, [el("span", { text: "КАССА, НАЧАЛО ДНЯ:" }), cashStart]),
      el("div", { class: "section", text: "Посещаемость" }),
      el(
        "div",
        { class: "list" },
        attendances.map((a) =>
          el(
            "div",
            {
              class: "card",
              onClick: () =>
                navigate("employeeAttendance", {
                  shiftId: data.id,
                  employeeId: a.id || a.employee_id,
                  name: a.full_name,
                }),
            },
            [
              el("div", { text: a.full_name }),
              el("div", {
                class: "meta",
                text: attendanceMeta(a),
              }),
            ]
          )
        )
      ),
      el("div", { class: "section", text: "Денежные операции" }),
      el(
        "div",
        { class: "list" },
        operations.map((op) =>
          el("div", { class: "card", text: op.display || op.display_text || `${op.category}` })
        )
      ),
      el("div", { class: "row", style: "margin-top:18px" }, [
        el("span", { text: "КАССА, КОНЕЦ ДНЯ:" }),
        cashEnd,
      ]),
      data.cash_mismatch && data.cash_end_set
        ? el("div", {
            class: "mismatch",
            text: `Расхождение кассы: ожидание ${formatRub(data.cash_expected_end)}, разница ${formatRub(data.cash_difference)}. Отмечено в отчётах.`,
          })
        : null,
    ]),
    el("div", { class: "footer-actions" }, [
      menuBtn("КАССА", () => navigate("cash", { shiftId: data.id })),
      menuBtn("ПОСЕЩАЕМОСТЬ", () => navigate("attendance", { shiftId: data.id })),
      menuBtn("ДЕНЕЖНЫЕ ОПЕРАЦИИ", () => navigate("moneyOps", { shiftId: data.id })),
    ]),
  ]);
}

function attendanceMeta(a) {
  if (a.absence) {
    return (
      { day_off: "ВЫХОДНОЙ", sick: "БОЛЬНИЧНЫЙ", vacation: "ОТПУСК" }[a.absence] || a.absence
    );
  }
  const events = a.events || [];
  if (!events.length) return "нет отметок";
  return events.map((e) => `${e.type === "arrival" ? "Приход" : "Уход"} ${e.time}`).join(" · ");
}

/* ---------- CASH ---------- */
async function renderCash() {
  if (!requireAuth()) return el("div");
  const shiftId = Number(route.params.shiftId);
  const startInput = el("input", { type: "text", inputmode: "decimal" });
  const endInput = el("input", { type: "text", inputmode: "decimal" });
  const screen = screenShell(
    "Касса",
    [
      el("div", { class: "field" }, [el("label", { text: "Касса, начало дня" }), startInput]),
      el("div", { class: "field" }, [el("label", { text: "Касса, конец дня" }), endInput]),
      el("button", {
        class: "btn",
        type: "button",
        text: "Сохранить",
        onClick: () => saveCash(false),
      }),
    ],
    { back: () => navigate("shift", { shiftId }) }
  );

  async function saveCash(forceSave) {
    try {
      const cashStart = moneyNormalize(startInput.value);
      const cashEnd = moneyNormalize(endInput.value);
      if (cashEnd && !cashStart) {
        toast("Сначала укажите кассу на начало дня");
        return;
      }
      const result = await ws.request("set_cash", {
        shift_id: shiftId,
        cash_start: cashStart,
        cash_end: cashEnd,
        update_start: true,
        update_end: true,
        force_save: forceSave,
      });
      if (result.needs_confirmation) {
        showModal({
          title: "Расхождение кассы",
          body: result.message || "Суммы не сходятся. Сохранить всё равно?",
          buttons: [
            { label: "Исправить" },
            { label: "Сохранить всё равно", primary: true, onClick: () => saveCash(true) },
          ],
        });
        return;
      }
      toast("Сохранено");
      navigate("shift", { shiftId });
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  }

  queueMicrotask(async () => {
    try {
      const data = await ws.request("get_shift", { shift_id: shiftId });
      if (data.cash_start_set) startInput.value = Number(data.cash_start).toFixed(2);
      if (data.cash_end_set) endInput.value = Number(data.cash_end).toFixed(2);
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });
  return screen;
}

/* ---------- ATTENDANCE ---------- */
async function renderAttendance() {
  if (!requireAuth()) return el("div");
  const shiftId = Number(route.params.shiftId);
  const list = el("div", { class: "list", text: "Загрузка…" });
  const screen = screenShell("Посещаемость", [list], {
    back: () => navigate("shift", { shiftId }),
  });
  queueMicrotask(async () => {
    try {
      const board = await ws.request("attendance_board", { shift_id: shiftId });
      list.innerHTML = "";
      for (const a of board.employees || []) {
        list.append(
          el(
            "div",
            {
              class: "card",
              onClick: () =>
                navigate("employeeAttendance", {
                  shiftId,
                  employeeId: a.id || a.employee_id,
                  name: a.full_name,
                }),
            },
            [el("div", { text: a.full_name }), el("div", { class: "meta", text: attendanceMeta(a) })]
          )
        );
      }
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });
  return screen;
}

async function renderEmployeeAttendance() {
  if (!requireAuth()) return el("div");
  const { shiftId, employeeId, name } = route.params;
  const status = el("div", { class: "section", text: "Статус" });
  const info = el("div", { class: "card", text: "Загрузка…" });
  const screen = screenShell(
    name || "Сотрудник",
    [
      info,
      el("button", {
        class: "btn",
        type: "button",
        text: "ПРИХОД",
        onClick: () => mark("set_arrival"),
      }),
      el("button", {
        class: "btn",
        type: "button",
        text: "УХОД",
        onClick: () => mark("set_departure"),
      }),
      el("div", { class: "section", text: "Отсутствие" }),
      el("div", { class: "toggle-row" }, [
        absenceBtn("ВЫХОДНОЙ", "day_off"),
        absenceBtn("БОЛЬНИЧНЫЙ", "sick"),
      ]),
      absenceBtn("ОТПУСК", "vacation"),
    ],
    { back: () => navigate("attendance", { shiftId }) }
  );

  let currentAbsence = null;

  function absenceBtn(label, kind) {
    return el("button", {
      class: "btn",
      type: "button",
      text: label,
      onClick: async () => {
        try {
          const next = currentAbsence === kind ? null : kind;
          const data = await ws.request("set_absence", {
            shift_id: Number(shiftId),
            employee_id: Number(employeeId),
            kind: next,
          });
          bindEmployee(data);
          toast(next ? "Отсутствие отмечено" : "Отсутствие снято");
        } catch (e) {
          toast(e.message || "Ошибка");
        }
      },
    });
  }

  function bindEmployee(data) {
    currentAbsence = data.absence || null;
    info.textContent = attendanceMeta(data) || "нет отметок";
    status.textContent = data.next_action
      ? `Далее: ${data.next_action === "arrival" ? "приход" : "уход"}`
      : "Статус";
  }

  async function mark(type) {
    const time = prompt("Время ЧЧ:ММ (пусто = сейчас)", "");
    if (time === null) return;
    try {
      const data = await ws.request(type, {
        shift_id: Number(shiftId),
        employee_id: Number(employeeId),
        time: time.trim() || null,
      });
      bindEmployee(data);
      toast("Сохранено");
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  }

  queueMicrotask(async () => {
    try {
      const board = await ws.request("attendance_board", { shift_id: Number(shiftId) });
      const emp = (board.employees || []).find(
        (e) => Number(e.id || e.employee_id) === Number(employeeId)
      );
      if (emp) bindEmployee(emp);
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });
  return screen;
}

/* ---------- MONEY OPS (SHIFT) ---------- */
async function renderMoneyOps() {
  if (!requireAuth()) return el("div");
  const shiftId = Number(route.params.shiftId);
  const list = el("div", { class: "list", text: "Загрузка…" });
  const screen = el("div", { class: "screen" }, [
    toolbar({ title: "Новая денежная операция", back: () => navigate("shift", { shiftId }), connected }),
    el("div", { class: "content" }, [
      menuBtn("Новый приход", () => navigate("newIncome", { shiftId })),
      menuBtn("Новый расход", () => navigate("newExpense", { shiftId })),
      el("div", { class: "section", text: "История за смену" }),
      list,
    ]),
  ]);
  queueMicrotask(async () => {
    try {
      const data = await ws.request("get_shift", { shift_id: shiftId });
      list.innerHTML = "";
      for (const op of data.operations || []) {
        list.append(
          el("div", {
            class: "card",
            text: op.display || `${op.category}`,
            onContextMenu: (ev) => {
              ev.preventDefault();
              confirmDeleteOp(op.id);
            },
            onDblClick: () => confirmDeleteOp(op.id),
          })
        );
      }
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });

  async function confirmDeleteOp(id) {
    showModal({
      title: "Удалить операцию?",
      body: "Действие необратимо.",
      buttons: [
        { label: "Отмена" },
        {
          label: "Удалить",
          primary: true,
          onClick: async () => {
            try {
              await ws.request("delete_operation", { operation_id: id });
              toast("Удалено");
              navigate("moneyOps", { shiftId });
            } catch (e) {
              toast(e.message || "Ошибка");
            }
          },
        },
      ],
    });
  }
  return screen;
}

function renderNewOperation(direction) {
  if (!requireAuth()) return el("div");
  const shiftId = Number(route.params.shiftId);
  const isIncome = direction === "income";
  const catSelect = el("select");
  const titleSelect = el("select");
  const titleWrap = el("div", { class: "field" }, [el("label", { text: "Название" }), titleSelect]);
  if (isIncome) titleWrap.hidden = true;
  const comment = el("textarea");
  const movementsBox = el("div");
  let vaults = [];
  let categories = [];
  let incomeTypes = [];
  const movements = [{ vaultId: null, amount: "" }];

  function redrawMovements() {
    movementsBox.innerHTML = "";
    movements.forEach((m, idx) => {
      const vaultSel = el("select");
      for (const v of vaults) {
        vaultSel.append(el("option", { value: String(v.id), text: v.name, selected: m.vaultId === v.id }));
      }
      if (!m.vaultId && vaults[0]) m.vaultId = vaults[0].id;
      vaultSel.value = String(m.vaultId || vaults[0]?.id || "");
      vaultSel.onchange = () => {
        m.vaultId = Number(vaultSel.value);
      };
      const amount = el("input", {
        type: "text",
        inputmode: "decimal",
        value: m.amount,
        placeholder: "Сумма",
      });
      amount.oninput = () => {
        m.amount = amount.value;
      };
      const remove = el("button", {
        class: "btn danger",
        type: "button",
        text: "✕",
        style: "min-height:44px;width:52px;margin:0",
        onClick: () => {
          if (movements.length === 1) return;
          movements.splice(idx, 1);
          redrawMovements();
        },
      });
      movementsBox.append(el("div", { class: "movement" }, [vaultSel, amount, remove]));
    });
    movementsBox.append(
      el("button", {
        class: "btn",
        type: "button",
        text: "+ Движение",
        onClick: () => {
          movements.push({ vaultId: vaults[0]?.id || null, amount: "" });
          redrawMovements();
        },
      })
    );
  }

  const screen = screenShell(
    isIncome ? "Новый приход" : "Новый расход",
    [
      el("div", { class: "field" }, [el("label", { text: "Вид" }), catSelect]),
      titleWrap,
      el("div", { class: "field" }, [el("label", { text: "Комментарий" }), comment]),
      el("div", { class: "section", text: "Движения" }),
      movementsBox,
      el("button", {
        class: "btn",
        type: "button",
        text: "Сохранить",
        onClick: async () => {
          try {
            const mov = [];
            for (const m of movements) {
              const amount = moneyNormalize(m.amount);
              if (!m.vaultId) throw new Error("Выберите хранилище");
              if (!amount) throw new Error("Укажите сумму");
              mov.push({ vault_id: m.vaultId, amount });
            }
            const category = catSelect.value;
            const title = isIncome ? category : titleSelect.value;
            await ws.request("create_operation", {
              shift_id: shiftId,
              direction,
              category,
              title,
              comment: comment.value || "",
              movements: mov,
            });
            toast(isIncome ? "Приход сохранён" : "Расход сохранён");
            navigate("moneyOps", { shiftId });
          } catch (e) {
            toast(e.message || "Ошибка");
          }
        },
      }),
    ],
    { back: () => navigate("moneyOps", { shiftId }) }
  );

  queueMicrotask(async () => {
    try {
      const refs = await ws.request("get_refs");
      vaults = refs.vaults || [];
      incomeTypes = refs.income_types || [];
      categories = refs.expense_categories || [];
      catSelect.innerHTML = "";
      if (isIncome) {
        for (const t of incomeTypes) catSelect.append(el("option", { value: t, text: t }));
      } else {
        for (const c of categories) catSelect.append(el("option", { value: c.name, text: c.name }));
        const fillTitles = () => {
          titleSelect.innerHTML = "";
          const items = categories.find((c) => c.name === catSelect.value)?.items || [];
          for (const i of items) titleSelect.append(el("option", { value: i, text: i }));
        };
        catSelect.onchange = fillTitles;
        fillTitles();
      }
      redrawMovements();
    } catch (e) {
      toast(e.message || "Ошибка справочников");
    }
  });
  return screen;
}

/* ---------- ARCHIVE ---------- */
async function renderArchive() {
  if (!requireAuth()) return el("div");
  const list = el("div", { class: "list", text: "Загрузка…" });
  const screen = screenShell("Архив смен", [list], { back: () => navigate("main") });
  queueMicrotask(async () => {
    try {
      const data = await ws.request("list_shifts");
      list.innerHTML = "";
      for (const item of data.items || []) {
        const details =
          session.isSuperuser &&
          (item.cash_flow_summary?.formula_text ||
            (item.cash_mismatch ? "⚠ Расхождение кассы" : ""));
        list.append(
          el(
            "div",
            {
              class: "card",
              onClick: () => navigate("shift", { shiftId: item.id }),
              onContextMenu: (ev) => {
                ev.preventDefault();
                deleteShift(item);
              },
            },
            [
              el("div", { text: item.title }),
              details ? el("div", { class: "meta", text: details }) : null,
            ]
          )
        );
      }
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });

  function deleteShift(item) {
    showModal({
      title: "Удалить смену?",
      body: "Удаление смены крайне не рекомендуется: это нарушит линейность информации в базе данных.",
      buttons: [
        { label: "Отмена" },
        {
          label: "Удалить",
          primary: true,
          onClick: async () => {
            try {
              await ws.request("delete_shift", { shift_id: item.id });
              toast("Смена удалена");
              navigate("archive");
            } catch (e) {
              toast(e.message || "Ошибка");
            }
          },
        },
      ],
    });
  }
  return screen;
}

/* ---------- ACCOUNTANT ---------- */
async function renderAccountantMoney() {
  if (!requireAuth() || !session.canAccessAccounting) return el("div");
  const list = el("div", { class: "list", text: "Загрузка…" });
  const screen = el("div", { class: "screen" }, [
    toolbar({ title: "ДЕНЕЖНЫЕ ОПЕРАЦИИ", back: () => navigate("main"), connected }),
    el("div", { class: "content" }, [
      menuBtn("Новый приход", () => navigate("accountantNew", { direction: "income" })),
      menuBtn("Новый расход", () => navigate("accountantNew", { direction: "expense" })),
      el("div", { class: "section", text: "Кассовые операции" }),
      list,
    ]),
  ]);
  queueMicrotask(async () => {
    try {
      const data = await ws.request("list_accountant_operations");
      list.innerHTML = "";
      for (const item of data.items || []) {
        list.append(
          el("div", {
            class: "card",
            text: item.display_text,
            onDblClick: () => deleteAccOp(item.id),
            onContextMenu: (ev) => {
              ev.preventDefault();
              deleteAccOp(item.id);
            },
          })
        );
      }
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });
  function deleteAccOp(id) {
    showModal({
      title: "Удалить операцию?",
      body: "",
      buttons: [
        { label: "Отмена" },
        {
          label: "Удалить",
          primary: true,
          onClick: async () => {
            await ws.request("delete_accountant_operation", { operation_id: id });
            toast("Удалено");
            navigate("accountantMoney");
          },
        },
      ],
    });
  }
  return screen;
}

function renderAccountantNew() {
  if (!requireAuth() || !session.canAccessAccounting) return el("div");
  const direction = route.params.direction || "income";
  const isIncome = direction === "income";
  const method = el("select", {}, [
    el("option", { value: "cash", text: "Кассовый" }),
    el("option", { value: "accrual", text: "Начисление" }),
  ]);
  const catSelect = el("select");
  const titleSelect = el("select");
  const titleField = el("div", { class: "field" }, [el("label", { text: "Название" }), titleSelect]);
  if (isIncome) titleField.hidden = true;
  const amount = el("input", { type: "text", inputmode: "decimal" });
  const counterparty = el("input", { type: "text" });
  const comment = el("textarea");
  let categories = [];

  const screen = screenShell(
    isIncome ? "Новый приход" : "Новый расход",
    [
      el("div", { class: "field" }, [el("label", { text: "Метод" }), method]),
      el("div", { class: "field" }, [el("label", { text: "Вид" }), catSelect]),
      titleField,
      el("div", { class: "field" }, [el("label", { text: "Сумма" }), amount]),
      el("div", { class: "field" }, [el("label", { text: "Контрагент" }), counterparty]),
      el("div", { class: "field" }, [el("label", { text: "Комментарий" }), comment]),
      el("button", {
        class: "btn",
        type: "button",
        text: "Сохранить",
        onClick: async () => {
          try {
            const category = catSelect.value;
            const title = isIncome ? category : titleSelect.value;
            const result = await ws.request("create_accountant_entry", {
              direction,
              method: method.value,
              category,
              title,
              amount: moneyNormalize(amount.value),
              counterparty: counterparty.value || "",
              comment: comment.value || "",
            });
            toast(result.type === "debt" ? "Задолженность создана" : "Операция сохранена");
            navigate("accountantMoney");
          } catch (e) {
            toast(e.message || "Ошибка");
          }
        },
      }),
    ],
    { back: () => navigate("accountantMoney") }
  );

  queueMicrotask(async () => {
    const refs = await ws.request("get_refs");
    catSelect.innerHTML = "";
    if (isIncome) {
      for (const t of refs.income_types || []) catSelect.append(el("option", { value: t, text: t }));
    } else {
      categories = refs.expense_categories || [];
      for (const c of categories) catSelect.append(el("option", { value: c.name, text: c.name }));
      const fill = () => {
        titleSelect.innerHTML = "";
        const items = categories.find((c) => c.name === catSelect.value)?.items || [];
        for (const i of items) titleSelect.append(el("option", { value: i, text: i }));
      };
      catSelect.onchange = fill;
      fill();
    }
  });
  return screen;
}

async function renderDebts() {
  if (!requireAuth() || !session.canAccessAccounting) return el("div");
  let kind = "receivable";
  const list = el("div", { class: "list" });
  const btnRecv = el("button", { class: "btn primary-fill", type: "button", text: "ДЕБИТОРСКАЯ" });
  const btnPay = el("button", { class: "btn", type: "button", text: "КРЕДИТОРСКАЯ" });

  async function load() {
    list.textContent = "Загрузка…";
    try {
      const data = await ws.request("list_debts", { kind });
      list.innerHTML = "";
      for (const item of data.items || []) {
        list.append(
          el("div", {
            class: "card",
            text: item.display_text,
            onDblClick: () => del(item.id),
            onContextMenu: (ev) => {
              ev.preventDefault();
              del(item.id);
            },
          })
        );
      }
      if (!(data.items || []).length) list.textContent = "Нет записей";
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  }

  function del(id) {
    showModal({
      title: "Удалить задолженность?",
      body: "",
      buttons: [
        { label: "Отмена" },
        {
          label: "Удалить",
          primary: true,
          onClick: async () => {
            await ws.request("delete_debt", { debt_id: id });
            toast("Удалено");
            load();
          },
        },
      ],
    });
  }

  btnRecv.onclick = () => {
    kind = "receivable";
    btnRecv.classList.add("primary-fill");
    btnPay.classList.remove("primary-fill");
    load();
  };
  btnPay.onclick = () => {
    kind = "payable";
    btnPay.classList.add("primary-fill");
    btnRecv.classList.remove("primary-fill");
    load();
  };

  const screen = screenShell(
    "ЗАДОЛЖЕННОСТИ",
    [el("div", { class: "toggle-row" }, [btnRecv, btnPay]), list],
    { back: () => navigate("main") }
  );
  queueMicrotask(load);
  return screen;
}

/* ---------- REPORTS ---------- */
function renderReports() {
  if (!requireAuth() || !session.isSuperuser) return el("div");
  let dateFrom = monthAgoIso();
  let dateTo = dateIso();
  const btnFrom = el("button", { class: "btn", type: "button", text: `С ${dateDisplay(dateFrom)}` });
  const btnTo = el("button", { class: "btn", type: "button", text: `ПО ${dateDisplay(dateTo)}` });

  function pick(isFrom) {
    const current = isFrom ? dateFrom : dateTo;
    const next = prompt("Дата ГГГГ-ММ-ДД", current);
    if (!next) return;
    if (isFrom) {
      dateFrom = next;
      btnFrom.textContent = `С ${dateDisplay(dateFrom)}`;
    } else {
      dateTo = next;
      btnTo.textContent = `ПО ${dateDisplay(dateTo)}`;
    }
  }
  btnFrom.onclick = () => pick(true);
  btnTo.onclick = () => pick(false);

  async function gen(type, title) {
    toast(`Формирую отчёт ${title}…`);
    try {
      const data = await ws.request("generate_report", {
        report_type: type,
        date_from: dateFrom,
        date_to: dateTo,
      });
      downloadBase64(data.filename, data.content_base64, data.mime);
      toast(`Готово: ${data.filename}`);
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  }

  return screenShell(
    "Отчёты",
    [
      el("div", { class: "toggle-row" }, [btnFrom, btnTo]),
      menuBtn("БАЛАНС", () => gen("balance", "БАЛАНС")),
      menuBtn("ОДДС", () => gen("odds", "ОДДС")),
      menuBtn("ОПиУ", () => gen("opiu", "ОПиУ")),
      menuBtn("РАСХОДЫ", () => gen("expenses", "РАСХОДЫ")),
      menuBtn("ПОСЕЩАЕМОСТЬ", () => gen("attendance", "ПОСЕЩАЕМОСТЬ")),
      menuBtn("LOGS", () => gen("logs", "LOGS")),
    ],
    { back: () => navigate("main") }
  );
}

/* ---------- ADMIN ---------- */
function renderAdmin() {
  if (!requireAuth() || !session.isSuperuser) return el("div");
  return screenShell(
    "Администрирование",
    [
      menuBtn("ПОЛЬЗОВАТЕЛИ", () => navigate("adminUsers")),
      menuBtn("ДОЛЖНОСТИ", () => navigate("adminPositions")),
      menuBtn("СОТРУДНИКИ", () => navigate("adminEmployees")),
      menuBtn("ДЕНЕЖНЫЕ ХРАНИЛИЩА", () => navigate("adminVaults")),
    ],
    { back: () => navigate("main") }
  );
}

async function renderAdminList(kind) {
  if (!requireAuth() || !session.isSuperuser) return el("div");
  const titles = {
    users: "Пользователи",
    positions: "Должности",
    employees: "Сотрудники",
    vaults: "Денежные хранилища",
  };
  const navMap = {
    users: "adminUsers",
    positions: "adminPositions",
    employees: "adminEmployees",
    vaults: "adminVaults",
  };
  const list = el("div", { class: "list", text: "Загрузка…" });
  const screen = screenShell(
    titles[kind],
    [
      menuBtn("ДОБАВИТЬ", () => addAdmin(kind, () => navigate(navMap[kind]))),
      list,
    ],
    { back: () => navigate("admin") }
  );

  queueMicrotask(async () => {
    try {
      const data = await ws.request("admin_list");
      list.innerHTML = "";
      const items =
        kind === "users"
          ? (data.users || []).map((u) => ({
              title: u.login,
              meta: `Роль: ${u.role} · ${u.display_name || "—"}`,
            }))
          : kind === "positions"
            ? (data.positions || []).map((p) => ({
                title: p.name,
                meta: p.is_active
                  ? p.no_schedule
                    ? "без графика"
                    : `${p.work_start_time || "—"}–${p.work_end_time || "—"}`
                  : "архив",
                raw: p,
              }))
            : kind === "employees"
              ? (data.employees || []).map((e) => ({
                  title: e.full_name,
                  meta: e.position_name || "без должности",
                }))
              : (data.vaults || []).map((v) => ({
                  title: v.name,
                  meta: v.is_cash ? "Касса (наличные)" : "Хранилище",
                }));
      for (const item of items) {
        const card = el("div", { class: `card${item.raw && !item.raw.is_active ? " muted" : ""}` }, [
          el("div", { text: item.title }),
          el("div", { class: "meta", text: item.meta }),
        ]);
        if (kind === "positions" && item.raw) {
          card.oncontextmenu = (ev) => {
            ev.preventDefault();
            togglePosition(item.raw, () => navigate("adminPositions"));
          };
        }
        list.append(card);
      }
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  });
  return screen;
}

function addAdmin(kind, reload) {
  if (kind === "users") {
    const login = el("input");
    const password = el("input", { type: "password" });
    const name = el("input");
    const role = el("select", {}, [
      el("option", { value: "user", text: "user" }),
      el("option", { value: "accountant", text: "accountant" }),
      el("option", { value: "superuser", text: "superuser" }),
    ]);
    showModal({
      title: "Новый пользователь",
      body: el("div", {}, [
        field("Логин", login),
        field("Пароль", password),
        field("Имя", name),
        field("Роль", role),
      ]),
      buttons: [
        { label: "Отмена" },
        {
          label: "Сохранить",
          primary: true,
          onClick: async () => {
            await ws.request("create_user", {
              login: login.value,
              password: password.value,
              display_name: name.value,
              role: role.value,
            });
            toast("Пользователь добавлен");
            reload();
          },
        },
      ],
    });
  } else if (kind === "positions") {
    const name = el("input");
    const start = el("input", { placeholder: "ЧЧ:ММ" });
    const end = el("input", { placeholder: "ЧЧ:ММ" });
    const noSchedule = el("input", { type: "checkbox" });
    showModal({
      title: "Новая должность",
      body: el("div", {}, [
        field("Название", name),
        field("Начало", start),
        field("Конец", end),
        el("label", {}, [noSchedule, " без графика"]),
      ]),
      buttons: [
        { label: "Отмена" },
        {
          label: "Сохранить",
          primary: true,
          onClick: async () => {
            await ws.request("create_position", {
              name: name.value,
              work_start_time: start.value || null,
              work_end_time: end.value || null,
              no_schedule: noSchedule.checked,
            });
            toast("Должность добавлена");
            reload();
          },
        },
      ],
    });
  } else if (kind === "employees") {
    const name = el("input");
    const pos = el("select");
    queueMicrotask(async () => {
      const data = await ws.request("admin_list");
      pos.append(el("option", { value: "", text: "— без должности —" }));
      for (const p of data.positions || []) {
        if (p.is_active) pos.append(el("option", { value: String(p.id), text: p.name }));
      }
    });
    showModal({
      title: "Новый сотрудник",
      body: el("div", {}, [field("ФИО", name), field("Должность", pos)]),
      buttons: [
        { label: "Отмена" },
        {
          label: "Сохранить",
          primary: true,
          onClick: async () => {
            await ws.request("create_employee", {
              full_name: name.value,
              position_id: pos.value ? Number(pos.value) : null,
            });
            toast("Сотрудник добавлен");
            reload();
          },
        },
      ],
    });
  } else if (kind === "vaults") {
    const name = el("input");
    const isCash = el("input", { type: "checkbox" });
    showModal({
      title: "Новое хранилище",
      body: el("div", {}, [field("Название", name), el("label", {}, [isCash, " Касса, наличные"])]),
      buttons: [
        { label: "Отмена" },
        {
          label: "Сохранить",
          primary: true,
          onClick: async () => {
            await ws.request("create_vault", { name: name.value, is_cash: isCash.checked });
            toast("Хранилище добавлено");
            reload();
          },
        },
      ],
    });
  }
}

function field(label, input) {
  return el("div", { class: "field" }, [el("label", { text: label }), input]);
}

function togglePosition(p, reload) {
  const next = !p.is_active;
  showModal({
    title: next ? "Восстановить должность?" : "Архивировать должность?",
    body: p.name,
    buttons: [
      { label: "Отмена" },
      {
        label: "Да",
        primary: true,
        onClick: async () => {
          await ws.request("set_position_active", { position_id: p.id, is_active: next });
          toast("Сохранено");
          reload();
        },
      },
    ],
  });
}

/* ---------- ROUTER ---------- */
async function render() {
  let node;
  switch (route.name) {
    case "login":
      node = renderLogin();
      break;
    case "main":
      node = renderMain();
      break;
    case "shift":
      node = await renderShift();
      break;
    case "cash":
      node = await renderCash();
      break;
    case "attendance":
      node = await renderAttendance();
      break;
    case "employeeAttendance":
      node = await renderEmployeeAttendance();
      break;
    case "moneyOps":
      node = await renderMoneyOps();
      break;
    case "newIncome":
      node = renderNewOperation("income");
      break;
    case "newExpense":
      node = renderNewOperation("expense");
      break;
    case "archive":
      node = await renderArchive();
      break;
    case "accountantMoney":
      node = await renderAccountantMoney();
      break;
    case "accountantNew":
      node = renderAccountantNew();
      break;
    case "debts":
      node = await renderDebts();
      break;
    case "reports":
      node = renderReports();
      break;
    case "admin":
      node = renderAdmin();
      break;
    case "adminUsers":
      node = await renderAdminList("users");
      break;
    case "adminPositions":
      node = await renderAdminList("positions");
      break;
    case "adminEmployees":
      node = await renderAdminList("employees");
      break;
    case "adminVaults":
      node = await renderAdminList("vaults");
      break;
    default:
      node = renderLogin();
  }
  appRoot.replaceChildren(node);
}

function boot() {
  unsub = ws.on((msg) => {
    if (msg.type === "connection") {
      connected = !!msg.connected;
      const dots = document.querySelectorAll(".status-dot");
      dots.forEach((d) => d.classList.toggle("on", connected));
    }
    if (msg.type === "auth_ok" && route.name === "login" && session.user) {
      navigate("main");
    }
    if (msg.type === "event" && msg.event === "shift_updated" && route.name === "shift") {
      if (!route.params.shiftId || Number(msg.payload?.id) === Number(route.params.shiftId)) {
        route.params.shiftId = msg.payload.id;
        appRoot.replaceChildren(buildShiftScreen(msg.payload));
      }
    }
    if (
      msg.type === "event" &&
      msg.event === "accountant_data_changed" &&
      (route.name === "accountantMoney" || route.name === "debts")
    ) {
      render();
    }
  });
  ws.connect();
  if (session.user) navigate("main");
  else navigate("login");
}

boot();
