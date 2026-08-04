/* 提交任务恢复层：页面关闭只断开观察连接，不影响应用级流水线。 */

let pipelineEventSource = null;
let pipelineTaskId = null;
let pipelineExitStage = 7;

function buildProgressSteps() {
  document.getElementById('progress-steps').innerHTML = stages.map((stage, index) => `
    <div class="progress-step" id="pstep-${index}">
      <div class="progress-step-dot"></div><span>${stage}</span>
    </div>`).join('');
}

function renderPipelineStage(stage, status = 'running') {
  for (let index = 0; index < stages.length; index++) {
    const element = document.getElementById(`pstep-${index}`);
    if (!element) continue;
    element.className = 'progress-step';
    if (index < stage - 1 || status === 'completed' && index < pipelineExitStage) element.classList.add('done');
    else if (index === stage - 1 && status === 'running') element.classList.add('active');
  }
}

async function attachPipelineTask(taskId) {
  if (pipelineEventSource) pipelineEventSource.close();
  pipelineTaskId = taskId;
  localStorage.setItem('lfv.pipelineTaskId', String(taskId));
  const progress = document.getElementById('submit-progress');
  const result = document.getElementById('submit-result');
  progress.classList.add('active');
  buildProgressSteps();
  result.textContent = '后台任务运行中；你可以离开此页面，稍后返回查看。';

  try {
    const taskResponse = await fetch(`/api/pipeline/tasks/${taskId}`);
    const task = await taskResponse.json();
    if (task.status === 'not_found') throw new Error('任务不存在');
    pipelineExitStage = task.exit_stage || 7;
    renderPipelineStage(task.current_stage, task.status);
    if (['completed', 'failed', 'cancelled'].includes(task.status)) {
      finishPipelineTask(task.status, task.error || '');
      return;
    }
  } catch (error) {
    result.textContent = `无法恢复任务: ${error.message}`;
    localStorage.removeItem('lfv.pipelineTaskId');
    return;
  }

  const source = new EventSource(`/api/pipeline/tasks/${taskId}/stream`);
  pipelineEventSource = source;
  source.onmessage = event => {
    const data = JSON.parse(event.data);
    if (data.type === 'progress') {
      renderPipelineStage(data.stage, data.status);
      result.textContent = `后台处理中 · ${data.stage_name || stages[data.stage - 1]}`;
    } else if (data.type === 'done') {
      source.close();
      finishPipelineTask(data.status, data.error || '');
    }
  };
  source.onerror = () => {
    // EventSource 会自动重连；后台任务不会因连接中断而停止。
    result.textContent = '正在重新连接后台任务…';
  };
}

function finishPipelineTask(status, error) {
  if (pipelineEventSource) pipelineEventSource.close();
  pipelineEventSource = null;
  const result = document.getElementById('submit-result');
  if (status === 'completed') {
    renderPipelineStage(pipelineExitStage, 'completed');
    result.textContent = pipelineExitStage === 7 ? '✓ 处理完成，已写入知识库。' : '✓ 已完成指定处理阶段。';
  } else {
    result.textContent = `✕ ${status === 'cancelled' ? '任务已取消' : '处理失败'}: ${error || '未知错误'}`;
  }
  localStorage.removeItem('lfv.pipelineTaskId');
  pipelineTaskId = null;
}

async function submitUrl() {
  const url = document.getElementById('submit-url').value.trim();
  if (!url) return;
  const result = document.getElementById('submit-result');
  result.textContent = '正在提交任务…';
  try {
    const template = document.getElementById('tpl-select').value;
    pipelineExitStage = currentMode === 'semi' ? parseInt(document.getElementById('exit-select').value) : 7;
    const response = await fetch('/api/pipeline/submit', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, mode: currentMode, exit_stage: pipelineExitStage, template}),
    });
    const data = await response.json();
    if (!response.ok || !data.task_id) throw new Error(data.error || '提交失败');
    attachPipelineTask(data.task_id);
  } catch (error) {
    result.textContent = `✕ 网络错误: ${error.message}`;
  }
}

const savedPipelineTaskId = parseInt(localStorage.getItem('lfv.pipelineTaskId') || '0');
if (savedPipelineTaskId) attachPipelineTask(savedPipelineTaskId);
