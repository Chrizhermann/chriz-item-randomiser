local script_path = assert(arg[0], "Lua test runner path missing"):gsub("\\", "/")
local test_directory = script_path:match("^(.*)/[^/]+$") or "."
local repository_root = test_directory .. "/../.."

local core_environment = {
    assert = assert,
    debug = debug,
    error = error,
    getmetatable = getmetatable,
    ipairs = ipairs,
    math = math,
    next = next,
    pairs = pairs,
    pcall = pcall,
    rawget = rawget,
    rawset = rawset,
    select = select,
    setmetatable = setmetatable,
    string = string,
    table = table,
    tonumber = tonumber,
    tostring = tostring,
    type = type,
    unpack = unpack or table.unpack,
    xpcall = xpcall,
    FLDLV = {},
}
core_environment._G = core_environment

local module_environment = {}
setmetatable(module_environment, { __index = _G })

local core_chunk, core_error = loadfile(repository_root .. "/copy/FLDLVCor.lua", "t", core_environment)
assert(core_chunk, core_error)
local Core = assert(core_chunk(), "FLDLVCor.lua did not return its module")

local fake_chunk, fake_error = loadfile(test_directory .. "/fake_engine.lua", "t", module_environment)
assert(fake_chunk, fake_error)
local FakeEngine = assert(fake_chunk(), "fake_engine.lua did not return its module")

local tests = {}
local function test(name, body)
    assert(type(name) == "string" and name:match("^[A-Za-z0-9_]+$"), "invalid test name")
    assert(type(body) == "function", "test body must be a function")
    tests[#tests + 1] = { name = name, body = body }
end

local function equal(actual, expected, message)
    if actual ~= expected then
        error((message or "values differ") .. ": expected=" .. tostring(expected) ..
            " actual=" .. tostring(actual), 2)
    end
end

local function truthy(value, message)
    if not value then
        error(message or "expected a truthy value", 2)
    end
end

local context = {
    Core = Core,
    FakeEngine = FakeEngine,
    test = test,
    equal = equal,
    truthy = truthy,
}

for _, filename in ipairs({
    "test_manifest.lua",
    "test_delivery.lua",
    "test_persistence.lua",
}) do
    local chunk, load_error = loadfile(test_directory .. "/" .. filename, "t", module_environment)
    assert(chunk, load_error)
    local register = assert(chunk(), filename .. " did not return its registration function")
    register(context)
end

local passed = 0
for _, case in ipairs(tests) do
    local ok, failure = xpcall(case.body, debug.traceback)
    if ok then
        passed = passed + 1
        io.write("PASS ", case.name, "\n")
    else
        io.stderr:write("FAIL ", case.name, "\n", tostring(failure), "\n")
    end
end

io.write(string.format("SUMMARY passed=%d failed=%d\n", passed, #tests - passed))
if passed ~= #tests then
    os.exit(1)
end
