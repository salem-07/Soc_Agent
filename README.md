# Retest Agent (05 / Retest) — SentinelX AI

إيجنت الـ **Retest** (الدور رقم 05 في مخطط SentinelX AI / Network First) مبني بالكامل حسب
العقد الموضّح في ملف `SentinelX_AI_Agent_Input_Output_Reference_EN.pdf` (Common input/output
+ جدول Role-specific input/output الخاص بصفحة "05 / Retest").

الإيجنت ده **جزء واحد بس من المشروع**: بياخد الـ input المحدد له من الـ Orchestrator (أو من أي
إيجنت تاني بيحط النتيجة في نفس الشكل)، يشغّل نموذج Gemini عشان يعمل الاستدلال (reasoning)
ويطلع VerificationResult، وبعدين **يتحقق ويفرض قواعد العقد برمجيًا** قبل ما يرجّع النتيجة —
مش بيسيب القرار النهائي للموديل وحده، بالظبط زي ما بيقول العقد:

> "The model proposes; deterministic services authorize, persist, dispatch... and decide
> authoritative finding/closure states."

## الملفات

| الملف | الوظيفة |
|---|---|
| `schemas.py` | Pydantic models دقيقة لكل حقول الـ Common input/output + RetestRoleInput + VerificationResult، منقولة حرفيًا من جدول الصفحة 05/Retest في الـ PDF. |
| `prompts.py` | الـ system prompt اللي بيحوّل دور Retest لتعليمات واضحة لـ Gemini + قواعد العقد (hard rules) + شكل الـ JSON المطلوب بالظبط. |
| `gemini_client.py` | Wrapper بسيط لاستدعاء Gemini API (`GEMINI_API_KEY` من environment variable فقط — مفيش مفاتيح في الكود). |
| `agent.py` | الـ orchestration logic: validate input → استدعاء Gemini → validate output → **enforcement طبقة فوق الموديل** لقواعد العقد (تحت). |
| `main.py` | تشغيل من الـ command line: بياخد ملف JSON أو stdin، يرجّع النتيجة النهائية JSON على stdout. مفيش أي تدخل يدوي مطلوب. |
| `server.py` | (اختياري) HTTP endpoint بسيط بـ FastAPI لو الـ Orchestrator بينادي كل دور كـ service منفصل. |
| `example_input.json` | مثال Input حسب "Network example" الموجود في صفحة الـ Retest بالـ PDF (firewall rule narrowing). |

## القواعد اللي بتتفرض برمجيًا (مش مجرد تعليمات للموديل)

دول القواعد اللي العقد بينص عليها صراحة لدور Retest، ومطبّقة في `agent.py` →
`_enforce_contract_rules` عشان ماتتعتمدش على التزام الموديل وحده:

1. **"A missing required check prevents PASS"** — لو مفيش `security_check_results` أو
   `functional_check_results`، أو فيه `related_variations` من غير `variation_check_results`،
   الكود بيحوّل الـ verdict لـ `INCONCLUSIVE` تلقائيًا مهما قال الموديل.
2. **"Twin verification cannot close a Real finding"** — لو `environment_kind` هو `TWIN`
   أو `BOTH`، الكود بيمنع أي `closure_recommendation` بيوحي بإغلاق finding حقيقي، ويحط
   `required_gate = REVIEW`.
3. **"Failed retest returns evidence for replanning; automatic reapplication is not
   assumed"** — لو `verdict = FAIL`، الكود بيتأكد إن فيه `next_objective` واضح للـ
   replanning، وميسيبش status `DONE`.
4. **عدم اختلاق IDs أو أدلة** — الـ prompt بيمنع الموديل من اختراع capability/evidence
   مش موجودة أصلاً في الـ input، وأي فشل في استدعاء Gemini نفسه بيرجّع `status=FAILED`
   مش verdict أمني مُخترَع.

## الربط مع باقي المشروع

الإيجنت بيستقبل بالظبط نفس الحقول اللي الـ PDF حاططها لدور Retest:

```json
{
  "common": { "...الحقول المشتركة زي ما هي في common input..." },
  "role_input": { "...original_finding_ref, change_ref, current_target_state_ref, ..." }
}
```

وبيرجّع:

```json
{
  "runtime": { "schema_version": "...", "workspace_id": "...", "model_call_id": "...", ... },
  "common_output": { "status": "...", "artifact_refs": [...], "required_gate": "...", ... },
  "verification_result": { "verdict": "PASS|FAIL|INCONCLUSIVE", "closure_recommendation": "...", ... }
}
```

ملاحظة مهمة: `runtime` (الـ `schema_version` / `workspace_id` / `model_call_id` ...) بيتحط
من الكود نفسه بعد الاستدعاء — مش من الموديل، بالظبط زي ما العقد بينص: "The trusted runtime
assigns or verifies these identifiers; the model cannot change them."

### تشغيل مباشر (import في نفس الـ process)

```python
from agent import RetestAgent

agent = RetestAgent()
result = agent.handle_task(task_input_dict)   # task_input_dict جاي من الـ Orchestrator
# ابعت result["artifact_refs"] / verification_result لباقي الأدوار زي ما العقد بيحدد
```

### تشغيل كـ CLI (لو الـ Orchestrator بيكتب ملف ويستنى الناتج)

```bash
export GEMINI_API_KEY="..."
python main.py task_input.json > verification_result.json
```

### تشغيل كـ HTTP service (لو الـ Orchestrator بينادي كل دور كـ microservice)

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="..."
uvicorn server:app --port 8005
# ثم: POST /v1/retest/run  مع الـ body = task_input_dict
```

## تجربة سريعة

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="your-key-here"
python main.py example_input.json
```

هيشغل الإيجنت تلقائيًا على المثال، يستدعي Gemini، يطبّق كل قواعد العقد، ويطبع النتيجة
النهائية جاهزة تتبعت لدور الـ Reporting & Memory أو ترجع للـ Orchestrator.

## اللي محتاج تضيفه إنت (على مستوى المشروع ككل، مش الإيجنت ده بس)

- جدول `available_capabilities` ده لازم يتجهز من خدمة الـ capability الحقيقية بتاعتكوا
  (مش موجود implementation للـ net.connect_check / net.service_health_check نفسهم هنا —
  دي "registered checks" خارج هذا الإيجنت حسب العقد).
- الـ persistence الفعلي لـ `artifact_refs` / تحديث حالة الـ finding في قاعدة البيانات —
  العقد بيقول صراحة "deterministic services authorize, persist... decide authoritative
  finding/closure states"، يعني ده مش شغل الإيجنت، ده شغل خدمة منفصلة بتستهلك الناتج ده.
