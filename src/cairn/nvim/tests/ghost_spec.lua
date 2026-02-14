local function reload(name)
  package.loaded[name] = nil
  return require(name)
end

describe('cairn ghost extmark rendering', function()
  local bufnr
  local cwd
  local tmpdir

  before_each(function()
    tmpdir = vim.fn.tempname()
    vim.fn.mkdir(tmpdir, 'p')
    cwd = vim.fn.getcwd()
    vim.cmd('cd ' .. vim.fn.fnameescape(tmpdir))

    package.loaded['cairn.config'] = nil
    package.loaded['cairn'] = nil
    require('cairn').setup({
      cairn_home = tmpdir,
      keymaps = { accept = false, reject = false, preview = false },
    })

    bufnr = vim.api.nvim_create_buf(true, false)
    vim.api.nvim_buf_set_name(bufnr, tmpdir .. '/app.lua')
  end)

  after_each(function()
    vim.cmd('cd ' .. vim.fn.fnameescape(cwd))
  end)

  it('renders add lines as virtual extmarks and clears them', function()
    local ghost = reload('cairn.ghost')
    ghost.show(bufnr, 'agent-12345678', {
      ['app.lua'] = {
        { type = 'add', line = 1, text = 'local x = 1' },
        { type = 'add', line = 3, text = 'return x' },
      },
    })

    local marks = vim.api.nvim_buf_get_extmarks(bufnr, ghost.ns, 0, -1, { details = true })
    assert.are.equal(2, #marks)

    ghost.clear(bufnr)
    local cleared = vim.api.nvim_buf_get_extmarks(bufnr, ghost.ns, 0, -1, { details = true })
    assert.are.equal(0, #cleared)
  end)
end)
