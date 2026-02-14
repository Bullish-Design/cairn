local M = {}

local function get_cairn_home()
  return require('cairn').config.cairn_home
end

local function ensure_parent(path)
  vim.fn.mkdir(vim.fn.fnamemodify(path, ':h'), 'p')
end

local function get_latest_agent()
  local state_file = get_cairn_home() .. '/state/latest_agent'
  local f = io.open(state_file, 'r')
  if not f then
    return nil
  end

  local agent_id = f:read('*line')
  f:close()

  if not agent_id or agent_id == '' then
    return nil
  end

  return agent_id
end

function M.queue(task, priority)
  local queue_file = get_cairn_home() .. '/queue/tasks.json'
  ensure_parent(queue_file)

  local tasks = {}
  if vim.fn.filereadable(queue_file) == 1 then
    tasks = vim.fn.json_decode(vim.fn.readfile(queue_file)) or {}
  end

  table.insert(tasks, {
    task = task,
    priority = priority and 'HIGH' or 'NORMAL',
    created_at = os.time(),
  })

  vim.fn.writefile({ vim.fn.json_encode(tasks) }, queue_file)
  vim.notify('Task queued: ' .. task, vim.log.levels.INFO)
end

function M.accept()
  local agent_id = get_latest_agent()
  if not agent_id then
    vim.notify('No agent to accept', vim.log.levels.WARN)
    return
  end

  local signal_file = get_cairn_home() .. '/signals/accept-' .. agent_id
  ensure_parent(signal_file)
  local f = assert(io.open(signal_file, 'w'))
  f:write(tostring(os.time()))
  f:close()

  vim.notify('Accepting agent ' .. agent_id:sub(1, 8), vim.log.levels.INFO)
end

function M.reject()
  local agent_id = get_latest_agent()
  if not agent_id then
    vim.notify('No agent to reject', vim.log.levels.WARN)
    return
  end

  local signal_file = get_cairn_home() .. '/signals/reject-' .. agent_id
  ensure_parent(signal_file)
  local f = assert(io.open(signal_file, 'w'))
  f:write(tostring(os.time()))
  f:close()

  vim.notify('Rejecting agent ' .. agent_id:sub(1, 8), vim.log.levels.INFO)
end

function M.preview()
  local agent_id = get_latest_agent()
  if not agent_id then
    vim.notify('No agent to preview', vim.log.levels.WARN)
    return
  end

  local current_file = vim.fn.expand('%:.')
  local current_line = vim.fn.line('.')

  require('cairn.tmux').open_preview(agent_id, current_file, current_line)
end

function M.list_tasks()
  local queue_file = get_cairn_home() .. '/queue/tasks.json'
  if vim.fn.filereadable(queue_file) == 0 then
    vim.notify('No tasks in queue', vim.log.levels.INFO)
    return
  end

  local tasks = vim.fn.json_decode(vim.fn.readfile(queue_file)) or {}
  local lines = { 'Queued Tasks:', '' }
  for i, task in ipairs(tasks) do
    table.insert(lines, string.format('%d. [%s] %s', i, task.priority or 'NORMAL', task.task or ''))
  end

  local chunks = {}
  for _, line in ipairs(lines) do
    table.insert(chunks, { line })
  end
  vim.api.nvim_echo(chunks, false, {})
end

function M.list_agents()
  local state_file = get_cairn_home() .. '/state/active_agents.json'
  if vim.fn.filereadable(state_file) == 0 then
    vim.notify('No active agents', vim.log.levels.INFO)
    return
  end

  local agents = vim.fn.json_decode(vim.fn.readfile(state_file)) or {}
  local lines = { 'Active Agents:', '' }
  for agent_id, info in pairs(agents) do
    table.insert(lines, string.format('%s: [%s] %s', agent_id:sub(1, 8), info.state or '?', info.task or ''))
  end

  local chunks = {}
  for _, line in ipairs(lines) do
    table.insert(chunks, { line })
  end
  vim.api.nvim_echo(chunks, false, {})
end

function M.select_agent(agent_id)
  local state_file = get_cairn_home() .. '/state/latest_agent'
  ensure_parent(state_file)
  local f = assert(io.open(state_file, 'w'))
  f:write(agent_id)
  f:close()

  vim.notify('Selected agent ' .. agent_id:sub(1, 8), vim.log.levels.INFO)
end

return M
