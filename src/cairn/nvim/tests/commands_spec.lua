local function reset_module(name)
  package.loaded[name] = nil
  return require(name)
end

describe('cairn command registration and file contracts', function()
  local tmp_home
  local orig_watcher

  before_each(function()
    vim.g.loaded_cairn = nil
    tmp_home = vim.fn.tempname()
    vim.fn.mkdir(tmp_home, 'p')

    orig_watcher = package.loaded['cairn.watcher']
    package.loaded['cairn.watcher'] = {
      setup = function() end,
      start = function() end,
    }

    package.loaded['cairn'] = nil
    dofile('cairn/nvim/plugin/cairn.lua')

    local cairn = reset_module('cairn')
    cairn.setup({
      cairn_home = tmp_home,
      keymaps = { accept = false, reject = false, preview = false },
    })
  end)

  after_each(function()
    package.loaded['cairn.watcher'] = orig_watcher
  end)

  it('registers user commands', function()
    local commands = vim.api.nvim_get_commands({})

    assert.is_truthy(commands.CairnQueue)
    assert.is_truthy(commands.CairnAccept)
    assert.is_truthy(commands.CairnReject)
    assert.is_truthy(commands.CairnPreview)
    assert.is_truthy(commands.CairnListTasks)
    assert.is_truthy(commands.CairnListAgents)
    assert.is_truthy(commands.CairnSelectAgent)
  end)

  it('creates queue and signal files through command handlers', function()
    local commands = reset_module('cairn.commands')

    commands.queue('Document helper methods', true)

    local queue_file = tmp_home .. '/queue/tasks.json'
    assert.are.equal(1, vim.fn.filereadable(queue_file))

    local tasks = vim.fn.json_decode(vim.fn.readfile(queue_file))
    assert.are.equal(1, #tasks)
    assert.are.equal('Document helper methods', tasks[1].task)
    assert.are.equal('HIGH', tasks[1].priority)

    local latest_agent_file = tmp_home .. '/state/latest_agent'
    vim.fn.mkdir(vim.fn.fnamemodify(latest_agent_file, ':h'), 'p')
    vim.fn.writefile({ 'agent-12345678' }, latest_agent_file)

    commands.accept()
    commands.reject()

    assert.are.equal(1, vim.fn.filereadable(tmp_home .. '/signals/accept-agent-12345678'))
    assert.are.equal(1, vim.fn.filereadable(tmp_home .. '/signals/reject-agent-12345678'))
  end)
end)
